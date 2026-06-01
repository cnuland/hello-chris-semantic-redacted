# Tester Agent -- Skills Inventory

## Skill 1: pytest Test Design

Design and implement structured test suites using pytest conventions.

**Capabilities:**
- Write pytest fixtures for shared test state (HTTP clients, base URLs, auth headers, test data corpora)
- Use `@pytest.mark.parametrize` to run the same assertion logic across multiple inputs (e.g., test all 5 sensitivity levels with one test function)
- Define custom markers for test categories: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.security`
- Use `conftest.py` for shared fixtures across test modules
- Structure test directories to mirror the service architecture

**Example fixture:**
```python
@pytest.fixture(scope="session")
def redaction_client():
    """HTTP client configured for the redaction service."""
    base_url = os.environ.get(
        "REDACTION_SERVICE_URL",
        "http://redaction-service.semantic-redacted.svc:8000",
    )
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
```

**Example parametrized test:**
```python
@pytest.mark.unit
@pytest.mark.parametrize("text,expected_entities", [
    ("Call Sarah Chen at 555-0123", ["PERSON", "PHONE_NUMBER"]),
    ("Email john@example.com about SSN 123-45-6789", ["EMAIL_ADDRESS", "US_SSN"]),
    ("Ship to 123 Main St, Springfield IL 62704", ["LOCATION"]),
])
def test_entity_detection(redaction_client, text, expected_entities):
    response = redaction_client.post("/analyze", json={"text": text})
    assert response.status_code == 200
    detected = {e["entity_type"] for e in response.json()["entities"]}
    for entity_type in expected_entities:
        assert entity_type in detected, f"Failed to detect {entity_type} in: {text}"
```

## Skill 2: HTTP Testing

Test live HTTP services using httpx or requests, validating response schemas, status codes, headers, and latency.

**Capabilities:**
- Send requests to live services running on the OpenShift cluster
- Validate JSON response schemas (required fields, correct types)
- Check HTTP status codes for success and error conditions
- Measure response latency for performance baselines
- Handle authentication headers and API key injection from environment variables
- Test both happy-path and error-path responses

**Example response validation:**
```python
@pytest.mark.integration
def test_sensitivity_classifier_response_schema(classifier_client):
    response = classifier_client.post("/classify", json={
        "text": "What is Sarah Chen's salary?",
    })
    assert response.status_code == 200
    body = response.json()
    assert "sensitivity_level" in body
    assert body["sensitivity_level"] in [
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED", "NEVER_EGRESS",
    ]
    assert "confidence" in body
    assert 0.0 <= body["confidence"] <= 1.0
    assert "anchor_distances" in body
```

**Example latency assertion:**
```python
@pytest.mark.integration
def test_redaction_latency(redaction_client):
    start = time.monotonic()
    response = redaction_client.post("/redact", json={
        "text": "Send the report to Jane Doe at jane.doe@acme.com",
    })
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert elapsed < 2.0, f"Redaction took {elapsed:.2f}s, expected < 2.0s"
```

## Skill 3: OpenShift CLI Testing

Use `oc` commands to verify cluster state, pod health, network policy enforcement, and audit trails.

**Capabilities:**
- Check pod status and readiness in the `semantic-redacted` namespace
- Execute commands inside pods using `oc exec` for egress verification
- Read pod logs with `oc logs` for audit trail validation
- Inspect NetworkPolicy resources and verify their selectors
- Verify Secret resources exist without exposing their values
- Check cross-namespace service reachability

**Example egress verification:**
```bash
# Verify non-gateway pod CANNOT reach external SaaS
oc exec -n semantic-redacted deployment/redaction-service -- \
    curl -s -o /dev/null -w "%{http_code}" \
    --connect-timeout 5 \
    https://generativelanguage.googleapis.com 2>&1 || echo "BLOCKED"

# Verify egress gateway pod CAN reach external SaaS
oc exec -n semantic-redacted deployment/egress-gateway -- \
    curl -s -o /dev/null -w "%{http_code}" \
    --connect-timeout 5 \
    https://generativelanguage.googleapis.com
```

**Example pod health check:**
```bash
# Verify all pods in semantic-redacted are Running
oc get pods -n semantic-redacted -o json | \
    jq -r '.items[] | select(.status.phase != "Running") | .metadata.name'
# Expected: no output (all pods running)
```

**Example audit log inspection:**
```bash
# Extract routing decision audit entries
oc logs -n semantic-redacted deployment/semantic-claw-router --tail=100 | \
    jq 'select(.event == "routing_decision")'
```

## Skill 4: Redaction Validation

Verify that the Presidio + GLiNER redaction pipeline correctly detects, replaces, and restores sensitive content.

**Capabilities:**
- Verify PII entities are replaced with deterministic placeholders
- Verify placeholder consistency within a session (same entity always maps to the same placeholder)
- Verify restore service correctly reverses pseudonymization
- Measure redaction recall against a labeled test corpus
- Verify that redaction preserves grammatical structure
- Detect false negatives (missed entities) and false positives (non-PII redacted)

**Example redaction round-trip test:**
```python
@pytest.mark.integration
def test_redact_and_restore_round_trip(redaction_client, restore_client):
    original = "Please contact Sarah Chen at sarah.chen@acme.com about Project Phoenix."

    # Step 1: Redact
    redact_resp = redaction_client.post("/redact", json={"text": original})
    assert redact_resp.status_code == 200
    redacted = redact_resp.json()
    assert "Sarah Chen" not in redacted["redacted_text"]
    assert "sarah.chen@acme.com" not in redacted["redacted_text"]
    assert "Project Phoenix" not in redacted["redacted_text"]
    assert "<PERSON_" in redacted["redacted_text"]
    assert "<EMAIL_" in redacted["redacted_text"]
    mapping_id = redacted["mapping_id"]

    # Step 2: Simulate SaaS response with placeholders
    saas_response = f"I have drafted the email to {redacted['redacted_text'].split()[2]}."

    # Step 3: Restore
    restore_resp = restore_client.post("/restore", json={
        "text": saas_response,
        "mapping_id": mapping_id,
    })
    assert restore_resp.status_code == 200
    restored = restore_resp.json()["restored_text"]
    assert "Sarah Chen" in restored or "<PERSON_" not in restored
```

**Example recall measurement:**
```python
@pytest.mark.unit
def test_redaction_recall(redaction_client):
    test_corpus = [
        {"text": "John Smith, SSN 123-45-6789", "entities": ["PERSON", "US_SSN"]},
        {"text": "Call 555-867-5309", "entities": ["PHONE_NUMBER"]},
        {"text": "Born on 03/15/1990", "entities": ["DATE_TIME"]},
        # ... extended corpus
    ]
    total_entities = 0
    detected_entities = 0
    for case in test_corpus:
        resp = redaction_client.post("/analyze", json={"text": case["text"]})
        detected_types = {e["entity_type"] for e in resp.json()["entities"]}
        for expected in case["entities"]:
            total_entities += 1
            if expected in detected_types:
                detected_entities += 1
    recall = detected_entities / total_entities
    assert recall > 0.95, f"Redaction recall {recall:.2%} is below 95% threshold"
```

## Skill 5: Sensitivity Classification Validation

Verify that the embedding-based sensitivity classifier assigns correct levels to known-sensitivity prompts.

**Capabilities:**
- Send prompts with known sensitivity levels and verify classification
- Test boundary conditions between adjacent levels
- Verify confidence scores are within expected ranges
- Validate anchor distance calculations
- Test with adversarial prompts designed to trick the classifier

**Example classification validation:**
```python
@pytest.mark.parametrize("prompt,expected_level", [
    ("What is the weather in New York?", "PUBLIC"),
    ("Summarize the Q3 product roadmap", "INTERNAL"),
    ("What is Sarah Chen's current salary?", "CONFIDENTIAL"),
    ("Show quarterly earnings before public filing", "REGULATED"),
    ("API key: sk-proj-abc123def456", "NEVER_EGRESS"),
])
def test_sensitivity_classification(classifier_client, prompt, expected_level):
    response = classifier_client.post("/classify", json={"text": prompt})
    assert response.status_code == 200
    result = response.json()
    assert result["sensitivity_level"] == expected_level, (
        f"Prompt '{prompt[:40]}...' classified as {result['sensitivity_level']}, "
        f"expected {expected_level}"
    )
```

**Example boundary test:**
```python
@pytest.mark.unit
def test_internal_vs_confidential_boundary(classifier_client):
    """Prompts near the INTERNAL/CONFIDENTIAL boundary should be classified
    conservatively (CONFIDENTIAL if ambiguous)."""
    borderline_prompts = [
        "Tell me about the team's project timeline",
        "What is the department budget allocation",
    ]
    for prompt in borderline_prompts:
        response = classifier_client.post("/classify", json={"text": prompt})
        level = response.json()["sensitivity_level"]
        assert level in ("INTERNAL", "CONFIDENTIAL"), (
            f"Borderline prompt classified as {level}, expected INTERNAL or CONFIDENTIAL"
        )
```

## Skill 6: NetworkPolicy Testing

Verify that Kubernetes NetworkPolicy resources enforce default-deny egress and restrict external access to the egress gateway pod only.

**Capabilities:**
- Enumerate all NetworkPolicy resources in the `semantic-redacted` namespace
- Verify default-deny egress policy exists and has correct selectors
- Test egress from each pod type to external SaaS endpoints
- Verify internal (intra-cluster) traffic is still permitted
- Verify cross-namespace traffic to `homelab-maas` works as expected

**Example NetworkPolicy enumeration:**
```python
@pytest.mark.security
def test_default_deny_egress_policy_exists():
    result = subprocess.run(
        ["oc", "get", "networkpolicy", "-n", "semantic-redacted", "-o", "json"],
        capture_output=True, text=True,
    )
    policies = json.loads(result.stdout)
    deny_policies = [
        p for p in policies["items"]
        if any(
            pt.get("policyTypes", []) == ["Egress"]
            or "Egress" in pt.get("policyTypes", [])
            for pt in [p["spec"]]
        )
    ]
    assert len(deny_policies) > 0, "No egress NetworkPolicy found in semantic-redacted"
```

**Example cross-pod egress test:**
```python
@pytest.mark.security
@pytest.mark.parametrize("deployment", [
    "redaction-service",
    "sensitivity-classifier",
    "nemo-guardrails",
])
def test_non_gateway_pods_cannot_egress(deployment):
    result = subprocess.run(
        [
            "oc", "exec", "-n", "semantic-redacted",
            f"deployment/{deployment}", "--",
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "--connect-timeout", "5",
            "https://generativelanguage.googleapis.com",
        ],
        capture_output=True, text=True, timeout=15,
    )
    # Expect either non-zero exit code (connection refused) or empty/error output
    assert result.returncode != 0 or result.stdout.strip() != "200", (
        f"{deployment} was able to reach external SaaS endpoint"
    )
```

## Skill 7: Demo Scenario Execution

Execute the 6 end-to-end demo scenarios as structured, reproducible test flows against the live cluster.

**Capabilities:**
- Execute each scenario as an HTTP request through the router entry point
- Capture the full request/response lifecycle including intermediate service calls
- Validate routing decisions by checking audit logs
- Verify redaction was applied (or not) as expected
- Produce structured scenario results with evidence

**Example scenario execution:**
```python
@pytest.mark.e2e
def test_scenario_4_sanitizable_query(router_client):
    """Scenario 4: PII is redacted, routed to Gemini, response restored."""
    response = router_client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [
            {"role": "user", "content": (
                "Draft an email to Sarah Chen about "
                "Project Phoenix deliverables"
            )},
        ],
    })
    assert response.status_code == 200
    body = response.json()

    # Response should contain the original names (restored)
    content = body["choices"][0]["message"]["content"]
    assert "Sarah Chen" in content or "<PERSON_" not in content

    # Check audit log for redaction evidence
    audit = get_latest_audit_entry(router_client)
    assert audit["sensitivity_level"] == "INTERNAL"
    assert audit["redaction_count"] > 0
    assert audit["target_model"] == "gemini"
    assert audit["redaction_restored"] is True
```

**Example scenario 6 (bypass attempt):**
```python
@pytest.mark.e2e
@pytest.mark.security
def test_scenario_6_bypass_attempt():
    """Scenario 6: Unauthorized SaaS call from non-gateway pod is blocked."""
    result = subprocess.run(
        [
            "oc", "exec", "-n", "semantic-redacted",
            "deployment/redaction-service", "--",
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "--connect-timeout", "5",
            "https://generativelanguage.googleapis.com",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0 or result.stdout.strip() in ("", "000"), (
        "Bypass succeeded: non-gateway pod reached external SaaS endpoint"
    )
```

## Skill 8: Bug Reporting

Produce structured, actionable bug reports that the Programmer can resolve without further clarification.

**Capabilities:**
- Capture full reproduction steps with exact commands and inputs
- Record expected vs actual behavior with specific values
- Include environment details (pod name, image tag, namespace, timestamp)
- Attach log excerpts and HTTP response bodies as evidence
- Assign severity based on impact to demo scenarios and security posture
- Suggest probable root cause when the failure pattern is recognizable

**Example structured bug report:**
```markdown
### BUG-001: Redaction service fails to detect phone numbers in parenthesized format

- **Severity:** HIGH
- **Affected component:** redaction-service
- **Acceptance criterion:** AC-7 (Redaction recall >95%)
- **Environment:**
  - Cluster: OpenShift 4.16
  - Namespace: semantic-redacted
  - Pod: redaction-service-7b4d9f8c6-xk2mn (image: redaction-service:v0.1.0)
  - Timestamp: 2026-05-24T14:32:00Z

#### Steps to Reproduce
1. POST to http://redaction-service.semantic-redacted.svc:8000/analyze
2. Body: {"text": "Call me at (555) 867-5309 for details"}
3. Observe the response entities list

#### Expected Result
Entity detected: PHONE_NUMBER at position 14-28

#### Actual Result
No PHONE_NUMBER entity in response. Only detected 0 entities total.
Response body: {"entities": [], "text": "Call me at (555) 867-5309 for details"}

#### Evidence
- HTTP response status: 200
- Full response: (see above)
- Same phone without parens "555-867-5309" IS detected correctly

#### Suggested Fix
Presidio's default PhoneRecognizer may not cover parenthesized US format.
Add a custom regex recognizer for pattern: \(\d{3}\)\s?\d{3}-\d{4}
```

## Skill 9: Coverage Assessment

Map every acceptance criterion to test coverage and identify gaps.

**Capabilities:**
- Build a traceability matrix from acceptance criteria IDs to test function names
- Identify acceptance criteria with no corresponding test
- Identify tests that do not map to any acceptance criterion (orphan tests)
- Calculate coverage percentage: (tested criteria) / (total criteria)
- Flag criteria that are partially tested (e.g., only happy path, no negative case)

**Example coverage matrix:**
```markdown
| AC-ID | Description | Test(s) | Status |
|-------|-------------|---------|--------|
| AC-1 | Sensitivity classifier returns 5 levels | test_sensitivity_classification | COVERED |
| AC-2 | Redaction recall >95% | test_redaction_recall | COVERED |
| AC-3 | Egress blocked for non-gateway pods | test_non_gateway_pods_cannot_egress | COVERED |
| AC-4 | Audit logs for every routing decision | test_audit_log_schema | COVERED |
| AC-5 | Restore service reverses pseudonymization | test_redact_and_restore_round_trip | COVERED |
| AC-6 | NeMo input rail blocks sensitive prompts | test_nemo_input_rail | COVERED |
| AC-7 | Demo scenario 1 passes | test_scenario_1_public_query | COVERED |
| AC-8 | No secrets in manifests | test_no_hardcoded_secrets | COVERED |
| AC-9 | Qwen in homelab-maas unaffected | test_qwen_still_serving | COVERED |
| AC-10 | Session pinning for sensitivity | — | GAP |
```

**Example gap analysis output:**
```
Coverage: 9/10 acceptance criteria covered (90%)
Gaps:
  - AC-10 (Session pinning for sensitivity): No test exists.
    Reason: Programmer noted session pinning is deferred to Phase 2.
    Recommendation: Add placeholder test marked @pytest.mark.skip with
    reason="Deferred to Phase 2 per programmer handoff"
```

## Tool & Library Requirements

The Tester uses the following tools and expects them available in the test environment:

| Tool | Purpose | Version |
|------|---------|---------|
| pytest | Test framework | >=7.0 |
| httpx | HTTP client for service testing | >=0.27 |
| jq | JSON processing for log inspection | >=1.6 |
| oc | OpenShift CLI for cluster interaction | >=4.14 |
| curl | HTTP calls from inside pods (via oc exec) | any |
| Python | Test code runtime | >=3.11 |
