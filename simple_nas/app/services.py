"""Which services offer a share: SMB, WebDAV, FTP - ticked per share in the share dialog.

Shares without the field get the defaults, so nothing changes for existing setups: SMB and WebDAV
on, as before. FTP is unencrypted and only offers the shares it is ticked for.
"""

DEFAULTS = {"smb": True, "webdav": True, "ftp": False}
SERVICES = tuple(DEFAULTS)


def share_services(share):
    saved = share.get("services")
    saved = saved if isinstance(saved, dict) else {}
    return {k: bool(saved.get(k, default)) for k, default in DEFAULTS.items()}


def offers(share, service):
    return share_services(share)[service]


def clean(value):
    """Request body -> stored dict; unknown keys dropped, missing ones get the defaults."""
    return share_services({"services": value})
