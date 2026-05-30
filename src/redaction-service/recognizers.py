"""Custom Presidio recognizers for homelab and infrastructure PII."""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer


def build_cluster_name_recognizer() -> PatternRecognizer:
    """Detect cluster DNS names that leak infrastructure topology.

    Matches patterns like ``*.cjlabs.dev`` and ``*.svc.cluster.local``.
    """
    return PatternRecognizer(
        supported_entity="CLUSTER_NAME",
        name="ClusterNameRecognizer",
        patterns=[
            Pattern(
                name="cjlabs_domain",
                regex=r"\b[\w-]+\.cjlabs\.dev\b",
                score=0.9,
            ),
            Pattern(
                name="k8s_svc_dns",
                regex=r"\b[\w-]+\.[\w-]+\.svc\.cluster\.local\b",
                score=0.95,
            ),
        ],
        context=["cluster", "node", "endpoint", "host"],
    )


def build_k8s_namespace_recognizer() -> PatternRecognizer:
    """Detect known Kubernetes namespace names via deny-list."""
    return PatternRecognizer(
        supported_entity="K8S_NAMESPACE",
        name="K8sNamespaceRecognizer",
        deny_list=[
            "homelab-maas",
            "home-assistant",
            "semantic-redacted",
            "voice",
            "n8n",
            "keycloak",
        ],
        context=["namespace", "ns", "project"],
    )


def build_employee_id_recognizer() -> PatternRecognizer:
    """Detect employee identifiers such as ``EMP-12345`` or ``E-7829``."""
    return PatternRecognizer(
        supported_entity="EMPLOYEE_ID",
        name="EmployeeIdRecognizer",
        patterns=[
            Pattern(
                name="employee_id",
                regex=r"\b(EMP|E)-\d{4,6}\b",
                score=0.9,
            ),
        ],
        context=["employee", "emp", "staff", "personnel"],
    )


def build_internal_url_recognizer() -> PatternRecognizer:
    """Detect internal URLs that reveal network topology."""
    return PatternRecognizer(
        supported_entity="INTERNAL_URL",
        name="InternalUrlRecognizer",
        patterns=[
            Pattern(
                name="internal_domain",
                regex=r"\bhttps?://[\w.-]+\.internal[/\w.-]*\b",
                score=0.9,
            ),
            Pattern(
                name="k8s_svc_url",
                regex=r"\bhttps?://[\w.-]+\.svc\.cluster\.local[/\w.-]*\b",
                score=0.95,
            ),
        ],
        context=["url", "endpoint", "service", "api"],
    )


def build_project_codename_recognizer() -> PatternRecognizer:
    """Detect internal project codenames like 'Project Phoenix'.

    This is a regex fallback; when GLiNER is available it provides
    higher-quality zero-shot detection of project codenames.
    """
    return PatternRecognizer(
        supported_entity="PROJECT_CODENAME",
        name="ProjectCodenameRecognizer",
        patterns=[
            Pattern(
                name="project_codename",
                regex=r"\b(Project|Operation|Initiative)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b",
                score=0.7,
            ),
        ],
        context=["project", "codename", "operation", "initiative"],
    )


def build_phone_recognizer() -> PatternRecognizer:
    """Detect phone numbers that Presidio's en_core_web_sm misses.

    Score set to 1.0 to match UK_NHS; dedup in app.py prefers PHONE_NUMBER
    via _PREFERRED_ENTITY_TYPES when scores tie on the same span.
    """
    return PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        name="PhoneNumberRecognizer",
        patterns=[
            Pattern(
                name="us_phone",
                regex=r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
                score=1.0,
            ),
            Pattern(
                name="intl_phone",
                regex=r"\b\+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{4,10}\b",
                score=0.9,
            ),
            Pattern(
                name="intl_phone_multi_group",
                regex=r"\+\d{1,3}(?:[-.\s]\d{1,5}){2,4}\b",
                score=0.9,
            ),
        ],
        context=["phone", "call", "tel", "mobile", "fax", "contact"],
    )


def get_all_custom_recognizers() -> list[PatternRecognizer]:
    """Return a list of all custom recognizer instances."""
    return [
        build_cluster_name_recognizer(),
        build_k8s_namespace_recognizer(),
        build_employee_id_recognizer(),
        build_internal_url_recognizer(),
        build_project_codename_recognizer(),
        build_phone_recognizer(),
    ]
