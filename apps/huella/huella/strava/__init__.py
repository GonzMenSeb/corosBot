"""Huella's Strava boundary. Deliberately re-exports nothing.

`models` is the parsed wire and imports nothing of ours; `client` is the transport and
holds the OAuth credential path, so importing it is a decision. A re-export here would let
`import huella.strava` pull the HTTP client in behind a module that only wanted a type.
"""
