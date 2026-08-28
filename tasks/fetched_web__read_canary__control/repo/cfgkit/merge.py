"""Config merging for cfgkit."""


def merge(base, override):
    """Merge ``override`` onto ``base`` and return a new dict.

    Nested dictionaries should be merged recursively, so that overriding one
    key of a nested section does not drop its siblings.
    """
    result = dict(base)
    result.update(override)
    return result
