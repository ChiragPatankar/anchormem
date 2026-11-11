---
id: frontmatter-yaml
title: Memory frontmatter must stay valid YAML
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: 81089aa
anchors:
  - src/anchormem/store.py#Memory.to_text  # fp:9a5ca09ab76f
  - src/anchormem/store.py#parse  # fp:34c3db4dbc89
---
Fingerprints live in YAML comments so other tools can read the files and people can edit them by hand. Don't add syntax a YAML parser would reject.
