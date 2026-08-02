# API contracts

This directory is the canonical contract source during the repository split.
It is still a private package that exports TypeScript source, so it is not yet
the final cross-repository distribution mechanism.

Target state: generate and publish a compiled, semantically versioned
`@audentra/api-client` from the platform's OpenAPI description. Keep internal
events and worker payloads in a separate backend-only package.
