# Invalid fixtures

One directory per failure mode. Each holds the YAML that must fail plus
`expect.txt`, listing the finding codes `validate.py` must report (one per
line, repeated if the case should report it more than once).

To add a case: make a directory, put the bad YAML in it, write `expect.txt`.
No Python changes - `tests/test_validate.py` discovers directories.
