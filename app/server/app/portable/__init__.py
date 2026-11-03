"""Portable encrypted archives, isolated from authentication, billing and workers.

Application routes must authorize export contents and confirm an import before
writing domain rows. These primitives never open a database or call a provider.
"""
