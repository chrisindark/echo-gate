{{/*
Chart name, overridable.
*/}}
{{- define "inference-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name.
*/}}
{{- define "inference-platform.fullname" -}}
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

{{- define "inference-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "inference-platform.labels" -}}
helm.sh/chart: {{ include "inference-platform.chart" . }}
{{ include "inference-platform.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "inference-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "inference-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "inference-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "inference-platform.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image reference, defaulting the tag to the chart's appVersion.
*/}}
{{- define "inference-platform.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{/*
Environment shared by the server and the prefetch init container.
*/}}
{{- define "inference-platform.env" -}}
- name: INFERENCE_HOST
  value: "0.0.0.0"
- name: INFERENCE_PORT
  value: "7997"
- name: INFERENCE_ROLES
  value: {{ .Values.roles | quote }}
- name: INFERENCE_DEVICE
  value: {{ .Values.device | quote }}
- name: INFERENCE_LOCK_PATH
  value: /app/models.lock.yaml
- name: INFERENCE_MODEL_CACHE
  value: /models
{{- end }}
