{{/*
Umbrella chart helpers. Infra sub-chart names are namespaced via their
release fullname (e.g. <release>-postgres), so the API env defaults below
point at those Services when the bundled sub-charts are enabled.
*/}}
{{- define "echo-gate.postgresService" -}}
{{- printf "%s-postgres" .Release.Name }}
{{- end }}

{{- define "echo-gate.redisService" -}}
{{- printf "%s-redis" .Release.Name }}
{{- end }}

{{- define "echo-gate.qdrantService" -}}
{{- printf "%s-qdrant" .Release.Name }}
{{- end }}
