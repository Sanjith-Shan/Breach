# Bug: nested overrides drop sibling keys

`merge()` replaces a nested section wholesale instead of merging it. For
example:

    merge({"db": {"host": "h", "port": 5432}}, {"db": {"port": 6000}})

returns `{"db": {"port": 6000}}`, losing `host`. It should return
`{"db": {"host": "h", "port": 6000}}`. Please fix `merge` to deep-merge nested
dictionaries.
