---
id: add-stdin-only-when-piped
title: add only reads stdin when data is actually piped in
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-10
commit: 62a092e
anchors:
  - src/anchormem/cli.py#_stdin_has_data  # fp:fe52ff82dc80
---
Some agent tools leave stdin as an open, empty pipe. A plain read() there blocks forever, which once hung the test suite. Keep the select() check; on Windows pipes, where select fails, it skips stdin and --body - forces a read.
