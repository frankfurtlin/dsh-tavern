package app.storyreader.personal;

import java.net.URI;
import java.util.Locale;

/** Per-document account highwater. No tokens, prose or durable identifiers. */
final class ReaderSpeechContextPolicy {
    static final String DOCUMENT_PROTOCOL = "reader-document-v1";
    static final long MAX_EPOCH = 9007199254740991L;
    static final class Ticket {
        final String owner, origin;
        final long epoch, generation;
        Ticket(String owner, String origin, long epoch, long generation) {
            this.owner = owner; this.origin = origin; this.epoch = epoch; this.generation = generation;
        }
    }
    private String owner = "", origin = "";
    private long epoch = -1, generation;
    private boolean ready, revoked;
    static boolean validNonce(String value) { return value != null && value.matches("[a-f0-9]{32}"); }
    static boolean sameDocument(String claimed, Object current) { return validNonce(claimed) && current instanceof String && claimed.equals(current); }
    static long epoch(Object raw) {
        if (!(raw instanceof Number)) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        double value = ((Number) raw).doubleValue();
        if (!Double.isFinite(value) || value < 0 || value > MAX_EPOCH || Math.rint(value) != value) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        return (long) value;
    }
    static String origin(String raw, boolean debug) {
        try {
            if (raw == null || raw.length() > 2048) throw new Exception();
            URI value = new URI(raw); String scheme = value.getScheme(), host = value.getHost(), path = value.getRawPath(); int port = value.getPort();
            if (host == null || host.isEmpty() || value.getRawUserInfo() != null || value.getRawQuery() != null || value.getRawFragment() != null
                || !(path == null || path.isEmpty() || path.equals("/")) || port == 0 || port < -1 || port > 65535 || value.getRawAuthority().contains("%")) throw new Exception();
            host = host.toLowerCase(Locale.ROOT);
            String loopback = host.startsWith("[") && host.endsWith("]") ? host.substring(1, host.length() - 1) : host;
            boolean local = loopback.equals("localhost") || loopback.equals("127.0.0.1") || loopback.equals("10.0.2.2") || loopback.equals("::1");
            if (!"https".equals(scheme) && !(debug && "http".equals(scheme) && local)) throw new Exception();
            if ("https".equals(scheme) && port == 443) port = -1;
            return scheme + "://" + host + (port == -1 ? "" : ":" + port);
        } catch (Exception ignored) { throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT"); }
    }
    synchronized boolean select(String nextOwner, String rawOrigin, long nextEpoch, boolean debug) {
        if (!validNonce(nextOwner) || nextEpoch < 0 || nextEpoch > MAX_EPOCH) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        String nextOrigin = origin(rawOrigin, debug);
        if (nextEpoch < epoch || (nextEpoch == epoch && (revoked || !owner.equals(nextOwner) || !origin.equals(nextOrigin)))) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
        boolean changed = nextEpoch != epoch || !owner.equals(nextOwner) || !origin.equals(nextOrigin);
        if (changed) { generation++; ready = false; }
        owner = nextOwner; origin = nextOrigin; epoch = nextEpoch; revoked = false;
        return changed;
    }
    synchronized void markReady() { if (revoked || owner.isEmpty()) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED"); ready = true; }
    synchronized void revoke(long nextEpoch) {
        if (nextEpoch < 0 || nextEpoch > MAX_EPOCH) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        if (nextEpoch < epoch || (nextEpoch == epoch && !revoked)) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
        if (nextEpoch != epoch || !revoked) generation++;
        epoch = nextEpoch; ready = false; revoked = true; owner = ""; origin = "";
    }
    synchronized void resetDocument() { generation++; epoch = -1; owner = ""; origin = ""; ready = false; revoked = false; }
    synchronized Ticket capture() { return ready ? new Ticket(owner, origin, epoch, generation) : null; }
    synchronized boolean valid(Ticket ticket, long suppliedEpoch) {
        return ticket != null && ready && !revoked && ticket.generation == generation && ticket.epoch == epoch && suppliedEpoch == epoch && owner.equals(ticket.owner) && origin.equals(ticket.origin);
    }
    synchronized long currentEpoch() { return epoch; }
    synchronized boolean revoked() { return revoked; }
}
