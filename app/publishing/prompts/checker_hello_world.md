# Quality Assurance Checker — Document Review Instructions

## Task

Review the provided SFI guidance document for pre-publication quality issues.

## Review Rules

1. **Broken Links** - Check for any URLs that appear invalid or non-functional. Flag with severity "high".

2. **Missing Sections** - Look for TODO comments or incomplete markers that indicate missing content (e.g., "TODO: Add X section"). Flag with severity "medium".

3. **Incomplete Steps** - Identify task steps that lack sufficient detail or context for users to complete. Flag with severity "medium".

4. **Formatting Issues** - Look for inconsistent formatting, missing punctuation, or structural problems. Flag with severity "low" to "medium".

5. **Clarity & Completeness** - Assess whether guidance is clear and complete. Identify sections where context gaps exist or language could be more precise. Flag with severity "medium".

6. **Spelling & Grammar** - Identify spelling mistakes or grammatical errors. Flag with severity "low".

## Output Format

For each finding, provide:
- `section`: Where in the document the issue is found
- `type`: Category of issue (broken_link, missing_section, incomplete_step, formatting, clarity, spelling)
- `severity`: "low", "medium", or "high"
- `message`: Clear description of the issue
- `recommendation`: Specific, actionable guidance for remediation
- `location`: Line number or context snippet

## Example

**Input Document:**
```
Title: Apply for SFI

Steps:
1. Log in
2. Complete form
3. Submit

Links: https://example.invalid/broken-link

TODO: Add eligibility requirements section
```

**Expected Findings:**
- Broken link in Links section (severity: high, recommend: "Update or remove")
- Missing eligibility requirements section (severity: medium, recommend: "Add section covering farm size, land type, and eligibility criteria")
- Step 2 lacks detail (severity: medium, recommend: "Specify which form, required fields, and supporting documents")
