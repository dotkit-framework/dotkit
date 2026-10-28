"""dotkit audit: the known vulnerabilities and malicious releases of the tools that dotkit installs.

The data comes from OSV.dev (https://osv.dev). The modules:

  model.py      the data that the sources and the core share: Item, Part, Advisory, Finding
  sources/      one module per kind of tool or package: the items and their OSV queries
  macho.py      the packages that a macOS executable was built from: Go modules, Rust crates
  match.py      the decisions: the advisories, notices, ranges, severity, the ignore rules. Runs no command
  cvss4.py      the CVSS 4.0 base score of a vector
  osv.py        the OSV API, with curl
  issues.py     one GitHub issue per finding, with gh
  cli.py        the command: the report, the JSON, the notification
"""
