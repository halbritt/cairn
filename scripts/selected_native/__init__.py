"""Selected native evidence reader (source only).

Pure modules: no process, socket, persistence-of-raw-streams, credential or provider entry. They select
delivered Cairn evidence and public action metadata from a native stream-json stream, keep terminal and
process facts outside the success-only grade path, and store selected evidence under an explicit intent.
Acceptance of this source does not establish authenticated exit, return, runtime admission or usefulness;
see docs/selected-native-reader.md for scope, limits and provenance.
"""
