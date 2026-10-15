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

{{/*
RFC 6335 Service port name: <=15 chars, ^[a-z]([-a-z0-9]*[a-z0-9])?, unique within a Service.
The service-name part is truncated rather than the whole string, so the port digits always
survive and two ports of the same service can never collapse onto one name; the protocol
letter keeps a tcp/udp pair sharing one port distinct.
Usage: include "helmgen.portName" (dict "name" $name "port" .containerPort "protocol" "TCP")
*/}}
{{- define "helmgen.portName" -}}
{{- $clean := regexReplaceAll "[^a-z0-9-]" (.name | lower) "-" -}}
{{- $clean := regexReplaceAll "-{2,}" $clean "-" -}}
{{- $n := trimAll "-" $clean | default "svc" -}}
{{- $n := regexMatch "^[a-z]" $n | ternary $n (printf "s%s" $n) -}}
{{- $proto := .protocol | default "TCP" | lower | trunc 1 -}}
{{- printf "%s-%d-%s" (trunc 8 $n | trimSuffix "-") (int .port) $proto | trunc 15 | trimSuffix "-" -}}
{{- end -}}

{{/*
Service port list, shared by the normal, headless and alias Services so they can
never disagree.
Usage: include "helmgen.servicePorts" (dict "name" $name "ports" $svc.ports)
*/}}
{{- define "helmgen.servicePorts" -}}
{{- $svcName := .name -}}
{{- range .ports }}
- port: {{ .containerPort }}
  targetPort: {{ .containerPort }}
  protocol: {{ .protocol | default "TCP" }}
  name: {{ include "helmgen.portName" (dict "name" $svcName "port" .containerPort "protocol" (.protocol | default "TCP")) | quote }}
{{- end }}
{{- end -}}
