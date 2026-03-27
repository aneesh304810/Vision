// ============================================================
// Jenkins Pipeline — Provision Developer VM on OpenShift
// Image: image-registry.openshift-image-registry.svc:5000/
//        is-prbk-airflow-q/airflow:latest
// ============================================================

pipeline {

    agent {
        label 'jenkins-agent-oc'   // agent with oc + kubectl CLI available
    }

    // ── Pipeline Parameters ──────────────────────────────────
    parameters {
        string(
            name: 'DEVELOPER_USERNAME',
            defaultValue: '',
            description: 'BBH AD username (e.g. jsmith). Used for VM name, namespace, and SSH config.'
        )
        choice(
            name: 'NAMESPACE',
            choices: ['is-prbk-airflow-d', 'is-prbk-airflow-q', 'is-prbk-airflow-p'],
            description: 'Target OpenShift namespace (dev / qa / prod)'
        )
        choice(
            name: 'CPU_REQUEST',
            choices: ['2', '4', '6', '8'],
            description: 'CPU cores requested for the developer VM pod'
        )
        choice(
            name: 'MEMORY_REQUEST',
            choices: ['4Gi', '8Gi', '12Gi', '16Gi'],
            description: 'Memory requested for the developer VM pod'
        )
        string(
            name: 'PVC_SIZE',
            defaultValue: '20Gi',
            description: 'Persistent volume size for developer workspace'
        )
        booleanParam(
            name: 'FORCE_RECREATE',
            defaultValue: false,
            description: 'Delete and recreate if VM already exists for this user'
        )
    }

    // ── Environment Variables ─────────────────────────────────
    environment {
        IMAGE            = 'image-registry.openshift-image-registry.svc:5000/is-prbk-airflow-q/airflow:latest'
        APP_LABEL        = "dev-vm-${params.DEVELOPER_USERNAME}"
        AIRFLOW_PORT     = '8080'
        JUPYTER_PORT     = '8888'
        CODE_SERVER_PORT = '8443'
        SSH_PORT         = '2222'
        OC_TOKEN         = credentials('openshift-sa-token')   // Jenkins credential ID
    }

    // ── Options ───────────────────────────────────────────────
    options {
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        ansiColor('xterm')
    }

    // ── Stages ────────────────────────────────────────────────
    stages {

        // ── Stage 1: Validate Inputs ─────────────────────────
        stage('Validate Inputs') {
            steps {
                script {
                    echo "==> Validating parameters..."

                    if (!params.DEVELOPER_USERNAME?.trim()) {
                        error "DEVELOPER_USERNAME is required. Please provide a BBH AD username."
                    }

                    // Only lowercase alphanumeric and hyphens allowed in k8s names
                    def validName = params.DEVELOPER_USERNAME ==~ /^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$/
                    if (!validName) {
                        error "DEVELOPER_USERNAME '${params.DEVELOPER_USERNAME}' is invalid. Use lowercase letters, numbers, hyphens only."
                    }

                    echo "✅ Developer username : ${params.DEVELOPER_USERNAME}"
                    echo "✅ Namespace          : ${params.NAMESPACE}"
                    echo "✅ CPU                : ${params.CPU_REQUEST} cores"
                    echo "✅ Memory             : ${params.MEMORY_REQUEST}"
                    echo "✅ PVC size           : ${params.PVC_SIZE}"
                    echo "✅ Image              : ${env.IMAGE}"
                }
            }
        }

        // ── Stage 2: Login to OpenShift ──────────────────────
        stage('OpenShift Login') {
            steps {
                script {
                    echo "==> Logging into OpenShift..."
                    sh """
                        oc login \
                            --token=\${OC_TOKEN} \
                            --server=https://api.openshift.bbh.com:6443 \
                            --insecure-skip-tls-verify=false

                        oc project ${params.NAMESPACE}
                        echo "✅ Logged in — namespace: ${params.NAMESPACE}"
                    """
                }
            }
        }

        // ── Stage 3: Check / Clean Existing Resources ────────
        stage('Check Existing Resources') {
            steps {
                script {
                    def username   = params.DEVELOPER_USERNAME
                    def namespace  = params.NAMESPACE
                    def exists     = sh(
                        script: "oc get deployment dev-vm-${username} -n ${namespace} --ignore-not-found -o name",
                        returnStdout: true
                    ).trim()

                    if (exists) {
                        if (params.FORCE_RECREATE) {
                            echo "⚠️  Existing deployment found — FORCE_RECREATE=true, deleting..."
                            sh """
                                oc delete deployment  dev-vm-${username}  -n ${namespace} --ignore-not-found
                                oc delete service     dev-vm-${username}  -n ${namespace} --ignore-not-found
                                oc delete configmap   dev-vm-${username}-config -n ${namespace} --ignore-not-found
                                # Note: PVC is intentionally preserved to protect developer data
                                echo "✅ Existing resources deleted (PVC preserved)"
                            """
                        } else {
                            error """
                                Deployment dev-vm-${username} already exists in ${namespace}.
                                Set FORCE_RECREATE=true to delete and recreate it.
                                Note: The existing PVC will be reused to preserve developer data.
                            """
                        }
                    } else {
                        echo "✅ No existing deployment found — proceeding with fresh provisioning"
                    }
                }
            }
        }

        // ── Stage 4: Create PVC ──────────────────────────────
        stage('Create PVC') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    // Check if PVC already exists (preserved from previous run)
                    def pvcExists = sh(
                        script: "oc get pvc dev-vm-${username}-workspace -n ${namespace} --ignore-not-found -o name",
                        returnStdout: true
                    ).trim()

                    if (pvcExists) {
                        echo "✅ PVC already exists — reusing (data preserved)"
                    } else {
                        echo "==> Creating PVC for ${username}..."
                        sh """
                            cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: dev-vm-${username}-workspace
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
    team: capital-partners
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: ${params.PVC_SIZE}
  storageClassName: thin-csi
EOF
                            echo "✅ PVC created: dev-vm-${username}-workspace"
                        """
                    }
                }
            }
        }

        // ── Stage 5: Create ConfigMap ────────────────────────
        stage('Create ConfigMap') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    echo "==> Creating ConfigMap for ${username}..."
                    sh """
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: v1
kind: ConfigMap
metadata:
  name: dev-vm-${username}-config
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
data:
  # Airflow
  AIRFLOW__CORE__EXECUTOR: "LocalExecutor"
  AIRFLOW__CORE__LOAD_EXAMPLES: "False"
  AIRFLOW__WEBSERVER__EXPOSE_CONFIG: "True"
  AIRFLOW__WEBSERVER__RBAC: "True"
  AIRFLOW_HOME: "/workspace/airflow"
  # dbt
  DBT_PROFILES_DIR: "/workspace/dbt"
  # Oracle
  ORACLE_HOME: "/opt/oracle/instantclient_21_13"
  LD_LIBRARY_PATH: "/opt/oracle/instantclient_21_13"
  ORACLE_HOST: "oracle-dev.bbh.com"
  ORACLE_PORT: "1521"
  ORACLE_SERVICE: "CPDW_DEV"
  ORACLE_SCHEMA: "${username.toUpperCase()}_DEV"
  # Developer identity
  DEVELOPER_USERNAME: "${username}"
  # code-server
  CS_DISABLE_GETTING_STARTED_OVERRIDE: "1"
EOF
                        echo "✅ ConfigMap created"
                    """
                }
            }
        }

        // ── Stage 6: Create Deployment ───────────────────────
        stage('Create Deployment') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE
                    def cpu       = params.CPU_REQUEST
                    def memory    = params.MEMORY_REQUEST

                    echo "==> Creating Deployment for ${username}..."
                    sh """
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dev-vm-${username}
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
    team: capital-partners
    provisioned-by: jenkins
  annotations:
    provisioned-at: "\$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    provisioned-by-build: "${env.BUILD_URL}"
spec:
  replicas: 1
  selector:
    matchLabels:
      app: dev-vm-${username}
  template:
    metadata:
      labels:
        app: dev-vm-${username}
        developer: ${username}
    spec:
      # ── Security Context ──────────────────────────────────
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000

      # ── Init Container: bootstrap workspace ───────────────
      initContainers:
        - name: init-workspace
          image: ${env.IMAGE}
          command: ["/bin/bash", "-c"]
          args:
            - |
              set -e
              echo "==> Bootstrapping workspace for ${username}..."

              # Airflow directories
              mkdir -p /workspace/airflow/dags \
                       /workspace/airflow/logs \
                       /workspace/airflow/plugins

              # dbt directories
              mkdir -p /workspace/dbt /workspace/.dbt

              # dbt profiles
              if [ ! -f /workspace/.dbt/profiles.yml ]; then
                cat > /workspace/.dbt/profiles.yml <<PROFILE
cpdw:
  target: dev
  outputs:
    dev:
      type: oracle
      host: \${ORACLE_HOST}
      port: 1521
      user: ${username}
      password: \${ORACLE_PASSWORD}
      service: \${ORACLE_SERVICE}
      schema: \${ORACLE_SCHEMA}
      threads: 4
PROFILE
              fi

              # Airflow init
              export AIRFLOW_HOME=/workspace/airflow
              export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////workspace/airflow/airflow.db
              if [ ! -f /workspace/airflow/airflow.db ]; then
                airflow db init
                airflow users create \
                  --username ${username} \
                  --password changeme123 \
                  --firstname Dev \
                  --lastname User \
                  --role Admin \
                  --email ${username}@bbh.com
                echo "✅ Airflow initialised"
              else
                echo "✅ Airflow DB already exists — skipping init"
              fi

              echo "==> Workspace bootstrap complete"
          envFrom:
            - configMapRef:
                name: dev-vm-${username}-config
          env:
            - name: ORACLE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: oracle-dev-credentials
                  key: password
          volumeMounts:
            - name: workspace
              mountPath: /workspace
          securityContext:
            runAsUser: 1000
            allowPrivilegeEscalation: false

      # ── Main Container ────────────────────────────────────
      containers:
        - name: dev-env
          image: ${env.IMAGE}
          imagePullPolicy: Always
          command: ["/bin/bash", "-c"]
          args:
            - |
              set -e

              echo "==> Starting services for ${username}..."

              # Airflow webserver
              airflow webserver --port 8080 --daemon
              echo "✅ Airflow webserver started"

              # Airflow scheduler
              airflow scheduler --daemon
              echo "✅ Airflow scheduler started"

              # JupyterLab
              jupyter lab \
                --ip=0.0.0.0 \
                --port=8888 \
                --no-browser \
                --NotebookApp.token='' \
                --NotebookApp.password='' \
                --notebook-dir=/workspace \
                --daemon 2>/dev/null
              echo "✅ JupyterLab started"

              # code-server (VS Code in browser)
              mkdir -p /workspace/.config/code-server
              cat > /workspace/.config/code-server/config.yaml <<CSCONFIG
bind-addr: 0.0.0.0:8443
auth: none
cert: false
user-data-dir: /workspace/.vscode-server
extensions-dir: /workspace/.vscode-extensions
CSCONFIG

              code-server \
                --bind-addr 0.0.0.0:8443 \
                --auth none \
                --user-data-dir /workspace/.vscode-server \
                --extensions-dir /workspace/.vscode-extensions \
                /workspace &
              echo "✅ code-server started"

              # Install VS Code extensions (first run only)
              if [ ! -d /workspace/.vscode-extensions/ms-python.python* ]; then
                code-server --install-extension ms-python.python
                code-server --install-extension innoverio.vscode-dbt-power-user
                code-server --install-extension ms-toolsai.jupyter
                code-server --install-extension redhat.vscode-yaml
                code-server --install-extension eamodio.gitlens
                code-server --install-extension mechatroner.rainbow-csv
                echo "✅ VS Code extensions installed"
              fi

              echo "==> All services running. Keeping container alive..."
              tail -f /workspace/airflow/logs/scheduler/latest/*.log 2>/dev/null || \
              sleep infinity

          envFrom:
            - configMapRef:
                name: dev-vm-${username}-config
          env:
            - name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
              value: "sqlite:////workspace/airflow/airflow.db"
            - name: ORACLE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: oracle-dev-credentials
                  key: password

          ports:
            - name: airflow
              containerPort: 8080
            - name: jupyter
              containerPort: 8888
            - name: codeserver
              containerPort: 8443

          volumeMounts:
            - name: workspace
              mountPath: /workspace

          resources:
            requests:
              cpu: "${cpu}"
              memory: "${memory}"
            limits:
              cpu: "${cpu}"
              memory: "${memory}"

          # ── Health Checks ───────────────────────────────
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 60
            periodSeconds: 15
            failureThreshold: 5

          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 120
            periodSeconds: 30
            failureThreshold: 3

          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]

      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: dev-vm-${username}-workspace

      restartPolicy: Always
EOF
                        echo "✅ Deployment created"
                    """
                }
            }
        }

        // ── Stage 7: Create Service ──────────────────────────
        stage('Create Service') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    echo "==> Creating Service for ${username}..."
                    sh """
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: v1
kind: Service
metadata:
  name: dev-vm-${username}
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
spec:
  selector:
    app: dev-vm-${username}
  type: ClusterIP
  ports:
    - name: airflow
      port: 8080
      targetPort: 8080
    - name: jupyter
      port: 8888
      targetPort: 8888
    - name: codeserver
      port: 8443
      targetPort: 8443
EOF
                        echo "✅ Service created"
                    """
                }
            }
        }

        // ── Stage 8: Create OpenShift Routes ─────────────────
        stage('Create Routes') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    echo "==> Creating Routes for ${username}..."
                    sh """
                        # code-server route (primary — developer uses this daily)
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: dev-vm-${username}-codeserver
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
spec:
  host: vscode-${username}.apps.openshift.bbh.com
  to:
    kind: Service
    name: dev-vm-${username}
  port:
    targetPort: codeserver
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF

                        # Airflow route
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: dev-vm-${username}-airflow
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
spec:
  host: airflow-${username}.apps.openshift.bbh.com
  to:
    kind: Service
    name: dev-vm-${username}
  port:
    targetPort: airflow
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF

                        # JupyterLab route
                        cat <<EOF | oc apply -f - -n ${namespace}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: dev-vm-${username}-jupyter
  namespace: ${namespace}
  labels:
    app: dev-vm-${username}
    developer: ${username}
spec:
  host: jupyter-${username}.apps.openshift.bbh.com
  to:
    kind: Service
    name: dev-vm-${username}
  port:
    targetPort: jupyter
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF
                        echo "✅ Routes created"
                    """
                }
            }
        }

        // ── Stage 9: Wait for Pod Ready ──────────────────────
        stage('Wait for Pod Ready') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    echo "==> Waiting for pod to be ready (timeout: 10m)..."
                    sh """
                        oc rollout status deployment/dev-vm-${username} \
                            -n ${namespace} \
                            --timeout=10m
                        echo "✅ Pod is running"
                    """
                }
            }
        }

        // ── Stage 10: Generate SSH Config ────────────────────
        stage('Generate SSH Config') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    // Get pod name
                    def podName = sh(
                        script: """
                            oc get pod -n ${namespace} \
                                -l app=dev-vm-${username} \
                                -o jsonpath='{.items[0].metadata.name}'
                        """,
                        returnStdout: true
                    ).trim()

                    echo "==> Pod name: ${podName}"

                    // Write SSH config snippet
                    def sshConfig = """
# ── BBH Dev Environment — ${username} ──────────────────────
# Add this block to: C:\\Users\\${username}\\.ssh\\config
# Then connect in VS Code: Remote-SSH: Connect to Host → dev-vm-${username}

Host dev-vm-${username}
    HostName ${podName}.${namespace}.svc.cluster.local
    User ${username}
    Port 2222
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ServerAliveInterval 60
    ServerAliveCountMax 3

# ── Or use oc port-forward (no SSH needed) ─────────────────
# Run this command on your laptop, then open URLs below:
#
# oc port-forward pod/${podName} 8080:8080 8888:8888 8443:8443 -n ${namespace}
#
# VS Code   → http://localhost:8443
# Airflow   → http://localhost:8080
# Jupyter   → http://localhost:8888
#
# ── Or use OpenShift Routes (direct browser access) ────────
# VS Code   → https://vscode-${username}.apps.openshift.bbh.com
# Airflow   → https://airflow-${username}.apps.openshift.bbh.com
# Jupyter   → https://jupyter-${username}.apps.openshift.bbh.com
"""
                    // Save as build artifact
                    writeFile(
                        file: "ssh-config-${username}.txt",
                        text: sshConfig
                    )

                    archiveArtifacts artifacts: "ssh-config-${username}.txt"

                    echo "✅ SSH config generated and archived as build artifact"
                    echo sshConfig
                }
            }
        }

        // ── Stage 11: Summary ────────────────────────────────
        stage('Summary') {
            steps {
                script {
                    def username  = params.DEVELOPER_USERNAME
                    def namespace = params.NAMESPACE

                    echo """
╔══════════════════════════════════════════════════════════════╗
║          Developer VM Provisioned Successfully               ║
╠══════════════════════════════════════════════════════════════╣
║  Developer  : ${username.padRight(46)}║
║  Namespace  : ${namespace.padRight(46)}║
║  CPU        : ${params.CPU_REQUEST.padRight(46)}║
║  Memory     : ${params.MEMORY_REQUEST.padRight(46)}║
║  PVC        : ${params.PVC_SIZE.padRight(46)}║
╠══════════════════════════════════════════════════════════════╣
║  ACCESS URLS                                                 ║
║  VS Code  → https://vscode-${username}.apps.openshift.bbh.com
║  Airflow  → https://airflow-${username}.apps.openshift.bbh.com
║  Jupyter  → https://jupyter-${username}.apps.openshift.bbh.com
╠══════════════════════════════════════════════════════════════╣
║  SSH config saved as build artifact: ssh-config-${username}.txt
║  Default Airflow login: ${username} / changeme123            ║
╚══════════════════════════════════════════════════════════════╝
"""
                }
            }
        }
    }

    // ── Post Actions ──────────────────────────────────────────
    post {
        failure {
            echo """
❌ Pipeline failed for ${params.DEVELOPER_USERNAME}
   Check the logs above for details.
   To retry: re-run with FORCE_RECREATE=true if resources were partially created.
"""
        }
        always {
            // Clean up oc session
            sh 'oc logout || true'
            cleanWs()
        }
    }
}
