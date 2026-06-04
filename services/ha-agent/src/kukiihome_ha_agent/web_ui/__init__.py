"""v2 product Web UI for the add-on ingress panel.

Lives at descriptive top-level paths (``/home``, ``/activity``, ``/areas``,
``/intent``, ``/policies``, ``/cameras``, ``/diagnostics``) so the legacy ``/``
status page remains untouched during the transition. Routed from
``__main__.py``; the home page is fleshed, the other pages are credible
skeletons that show what's coming.

Anchored on the principles + IA ratified in
``planning/web-ui-design.md`` (Parts I-III ratified, IV-IX in progress).
"""
