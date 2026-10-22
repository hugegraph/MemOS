# Dependency and license scope

The live BFF adds two direct JavaScript runtime dependencies:

| Package | Purpose | License |
| --- | --- | --- |
| `ajv` | OpenAPI-derived JSON Schema validation | MIT ([license](https://github.com/ajv-validator/ajv/blob/master/LICENSE)) |
| `ajv-formats` | Standard JSON Schema formats | MIT ([license](https://github.com/ajv-validator/ajv-formats/blob/master/LICENSE)) |

Their package license files are retained by the runtime image's `npm ci` installation.
The Docker runtime uses its own exact-version lockfile; root `pnpm-lock.yaml` covers development
and tests. Existing frontend package versions remain at the approved upstream lockfile versions;
adding the validator packages also updates optional-peer resolution metadata.

Python overlays reuse packages already present in the compatible MemOS/gateway images rather
than silently installing a different model/storage stack. The base images and their dependencies
retain their existing licenses. New source follows this repository's existing Apache-2.0 licensing;
it is not presented as an independently ASF-owned project.

Redocly CLI is a pinned validation tool used by CI, not a BFF production dependency. When changing
either lockfile or base images, review transitive licenses and distribution contents as well as
direct dependencies. This table is the change scope, not a complete product SBOM.
