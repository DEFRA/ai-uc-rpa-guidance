---
title: technical-a-to-z — A
description: See a list of the style points for technical content on GOV.UK, including how to spell and format words and phrases.
---

### API

Do not expand the abbreviation for technical users.

---

### API endpoints

Use `methodname endpoint` for an API endpoint. Do not include the base path. Use {CAPS\_WITH\_UNDERSCORES\_INSIDE\_CURLY\_BRACKETS} for placeholder parameters in endpoints.

For example:

GET /v1/payments/{PAYMENT\_ID}/

Replace:

* `{PAYMENT_ID}` with the ID of the payment you’re checking
* `{REFUND_ID}` with the ID of the refund you’re checking

---

### API headers

There are lots of HTTP and API headers, so use code style and the exact name of API headers, to make it clear which header you mean. For example:

* `Authorisation` header
* `Content-type` header - not `Content header` because there are several different content headers

Example:

You must include an `Authorisation` header in your request.

---

### API key

Do not expand the abbreviation for technical users. Use:

* create an API key – not generate
* revoke an API key

---

### API parameters and fields

Use:

* parameter for API request items, not ‘option’
* field for API response items, not ‘variable’
* object, not ‘dictionary’ or ‘array’ – for example: If the `status` in the `refund_summary` object is `available`…
* key
* value
* key-value pair

Parameters are required or optional. Do not use ‘you do not need’ (which is ambiguous) or ‘you can leave out’.

---

### API requests

Use ‘API request’ not ‘API call’.

Tell users they can ‘include’ a parameter in an API request, not that they can ‘supply’ a parameter.

[Back to top](#content)

---

## B

---

### (a) terminal

Not ‘the command-line’ or ‘the command-line interface’, or ‘Terminal’ (which is the macOS terminal specifically).