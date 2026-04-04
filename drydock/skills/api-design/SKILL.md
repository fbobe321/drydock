---
name: api-design
description: Design and review REST/GraphQL APIs. Schema validation, endpoint design, error handling patterns.
allowed-tools: read_file grep glob write_file bash
user-invocable: true
---

# API Design Review

Analyze or design an API ($ARGUMENTS).

## For existing APIs:
1. Find route/endpoint definitions
2. Check for consistency in naming, HTTP methods, status codes
3. Verify error handling and validation
4. Check authentication/authorization
5. Review request/response schemas

## For new APIs:
1. Define resources and relationships
2. Design endpoints following REST conventions
3. Define request/response schemas
4. Plan error codes and messages
5. Generate OpenAPI/Swagger spec if requested

## Best Practices
- Use plural nouns for resources (/users, not /user)
- Use HTTP methods correctly (GET=read, POST=create, PUT=update, DELETE=delete)
- Return appropriate status codes (201 for create, 204 for delete)
- Use consistent error format: `{"error": {"code": "...", "message": "..."}}`
- Version the API (/v1/, /v2/)
- Paginate list endpoints
