{{/*
Selector labels for a service.
Usage: include "helmgen.selectorLabels" (dict "root" $ "name" $name)
*/}}
{{- define "helmgen.selectorLabels" -}}
app: {{ .root.Release.Name }}-{{ .name }}
{{- end -}}

{{/*
Standard metadata labels for a service.
Usage: include "helmgen.labels" (dict "root" $ "name" $name)
*/}}
{{- define "helmgen.labels" -}}
{{ include "helmgen.selectorLabels" . }}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/component: {{ .name }}
app.kubernetes.io/part-of: {{ .root.Chart.Name }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version }}
helmgen.io/generated: "true"
{{- end -}}
