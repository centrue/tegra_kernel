# Copyright (c) 2026. Jetpack workspace contributors.
# SPDX-License-Identifier: MIT

"""Group VC MIPI profile overlays using metadata stored in each DTBO."""

from Utils import dtc


PROFILE_PROPERTY = 'vc-mipi-profile'
PARENT_PROPERTY = 'vc-mipi-parent'
MODEL_PROPERTY = 'vc-mipi-model'


def _profile_sort_key(profile):
    model = profile['model']
    return (model == 'Auto Detect', model)


def group(hw_addons):
    """Return ordinary overlay names and metadata-driven profile groups."""
    ordinary = []
    groups = {}

    for name, path in hw_addons.items():
        profile = dtc.get_prop_value(path, '/', PROFILE_PROPERTY, 0)
        if profile != 'custom':
            ordinary.append(name)
            continue

        parent = dtc.get_prop_value(path, '/', PARENT_PROPERTY, 0)
        model = dtc.get_prop_value(path, '/', MODEL_PROPERTY, 0)
        if not parent or not model:
            raise RuntimeError(
                "VC MIPI profile metadata is incomplete in %s" % path)
        groups.setdefault(parent, []).append({
            'model': model,
            'name': name,
            'path': path,
        })

    for parent in groups:
        groups[parent].sort(key=_profile_sort_key)
    return sorted(ordinary), groups


def resolve(hw_addons, selection):
    """Resolve an exact overlay name or a parent/model profile selection."""
    if selection in hw_addons:
        return selection

    unused, groups = group(hw_addons)
    normalized = selection.strip()
    for parent, profiles in groups.items():
        for profile in profiles:
            aliases = (
                "%s/%s" % (parent, profile['model']),
                "%s / %s" % (parent, profile['model']),
            )
            if normalized in aliases:
                return profile['name']
    return None
