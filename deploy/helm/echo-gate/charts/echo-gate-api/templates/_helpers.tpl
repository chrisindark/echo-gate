{{/*
Chart name, overridable.
*/}}
{{- define "echo-gate-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name.
*/}}
{{- define "echo-gate-api.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "echo-gate-api.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "echo-gate-api.labels" -}}
helm.sh/chart: {{ include "echo-gate-api.chart" . }}
{{ include "echo-gate-api.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "echo-gate-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "echo-gate-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "echo-gate-api.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "echo-gate-api.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "echo-gate-api.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{/*
Non-secret env shared by the API, worker, beat, and migration pods.
Database passwords come from a Secret (existingSecret) — they are NOT
rendered from values.yaml. Placeholders __DB_APP_PASSWORD__ and
__DB_OWNER_PASSWORD__ in the URLs are substituted by the init flow below.
*/}}
{{- define "echo-gate-api.env" -}}
- name: PYTHON_ENV
  value: {{ .Values.config.pythonEnv | quote }}
- name: CACHE_MODE
  value: {{ .Values.config.cacheMode | quote }}
- name: DEFAULT_LLM_SERVICE
  value: {{ .Values.config.defaultLlmService | quote }}
- name: INFERENCE_BASE_URL
  value: {{ .Values.config.inferenceBaseUrl | quote }}
- name: REDIS_URL
  value: {{ .Values.config.redisUrl | quote }}
- name: CELERY_BROKER_URL
  value: {{ .Values.config.celeryBrokerUrl | quote }}
- name: QDRANT_URL
  value: {{ .Values.config.qdrantUrl | quote }}
- name: DB_APP_ROLE
  value: {{ .Values.config.dbAppRole | quote }}
- name: USE_REDIS_EXACT_MATCHING
  value: {{ .Values.config.useRedisExactMatching | quote }}
- name: USE_QDRANT_EXACT_MATCHING
  value: {{ .Values.config.useQdrantExactMatching | quote }}
- name: USE_QDRANT_SEMANTIC_MATCHING
  value: {{ .Values.config.useQdrantSemanticMatching | quote }}
- name: TELEMETRY_ENABLED
  value: {{ .Values.config.telemetryEnabled | quote }}
{{- if .Values.config.telemetryEndpoint }}
- name: TELEMETRY_ENDPOINT
  value: {{ .Values.config.telemetryEndpoint | quote }}
{{- end }}
{{- end }}
