"""ok-script's Web runtime, kept here because ok-script removed it after 2.0.6.

``app.py`` is ``ok/ui/web/app.py``, ``task_tabs.py`` is ``ok/task/web.py`` and
``requirements.py`` is ``ok/ui/web/requirements.py`` of ok-script 2.0.6
(https://github.com/ok-oldking/ok-script), under the license in
``LICENSES/ok-script-2.0.6.txt``. They differ from it only in their imports and in
``app.py`` no longer serving ok-script's own page and icon from a ``static`` folder.
``server_logging.py`` is the log routing part of ``ok/ui/web/server.py``.

Kept as close to the original as possible so that it can be compared; the desktop
client's own behaviour belongs in ``src/desktop/app.py``.
"""
