# Versioning

## Grows with no bump and no doc change

| Want | Do |
|---|---|
| A new provider | One class, one declaration. Its group is configuration. |
| A new kind | One line under `x-kinds` in `<type>.schema.json`, plus a shape file when none fits. |
| A new optional field or enum value | Edit the shape file. One line in `CHANGELOG.md`. |
| A new subcategory | Use the slug. The vocabulary is a pattern, not a list. |
| A new id scheme | A key under `ids`. |
| A new type | A `<type>.schema.json` and a module. Apps ignore unknown types. |

Core-owned enums (`access`, `license.code`) grow by one changelog line per value. The app treats an unknown value
as "no Play".

## What bumps what

| Change | Bump |
|---|---|
| Kind, optional field, enum value, type added | none |
| Required field added, or anything removed | that type's `x-version` |
| Envelope field added | none |
| Envelope field renamed or removed | every type, and `orman_tv: 2` |

The app refuses a newer major and ignores an unknown type. A bump needs a decision record (`GOVERNANCE.md`).
