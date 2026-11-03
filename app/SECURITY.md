# Security

Do not put production credentials, provider keys, private novels or database dumps in issues or pull requests. Report suspected vulnerabilities through GitHub's private vulnerability reporting when available; otherwise contact the repository owner without posting exploit details publicly.

Generate a private `.env` with `python scripts/init-env.py`. Keep the encryption master key and signing secret stable across restarts. Back up the database and private object volume together; losing the encryption master key prevents decrypting stored provider credentials.

Admin rights require an explicit server-side CLI action. Registration never grants ADMIN. Analytics APIs check ADMIN independently of the UI.

The quick start binds to loopback. Before making a deployment public, set its HTTPS origin, cookie settings, model allowlist, registration policy and intended quota policy. Unlimited mode removes product quotas; provider-side billing still applies.
