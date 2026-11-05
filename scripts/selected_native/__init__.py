"""Selected native evidence reader (source only).

Pure modules: no process, socket, persistence-of-raw-streams, credential, no provider. They select
delivered Cairn evidence and public action metadata from a native stream-json stream, keep terminal and
process facts outside the success-only grade path, and store selected evidence under an explicit intent.
Acceptance of this source does not establish authenticated exit, return, not runtime admission or usefulness;
retention exit basis is caller_supplied_not_independently_attested.
"""
