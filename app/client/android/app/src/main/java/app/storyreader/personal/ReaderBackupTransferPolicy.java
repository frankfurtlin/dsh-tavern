package app.storyreader.personal;

import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;

/** Platform-free scope, origin and framing limits. No credentials are stored. */
public final class ReaderBackupTransferPolicy {
    public static final long MAX_CIPHERTEXT_BYTES = 2L * 1024 * 1024 * 1024 + 64L * 1024 * 1024;
    public static final long MAX_FILE_BYTES = MAX_CIPHERTEXT_BYTES + 128;
    public static final long MAX_READING_BYTES = 2L * 1024 * 1024 * 1024;
    public static final String DOCUMENT_PROTOCOL = "reader-document-v1";
    public static final int HEADER_BYTES = 58;
    public static final int CHUNK_BYTES = 64 * 1024;
    public static final int MAX_RESPONSE_BYTES = 1024 * 1024;
    public static final long MAX_EPOCH = 9007199254740991L;

    public static final class Failure extends IOException {
        public final String code;
        public Failure(String code) { super(code); this.code = code; }
    }
    public static final class Context {
        public final String backend, ownerId;
        public final long epoch, generation;
        private Context(String backend, String ownerId, long epoch, long generation) {
            this.backend = backend; this.ownerId = ownerId; this.epoch = epoch; this.generation = generation;
        }
    }
    public static final class Ticket {
        public final Context context;
        public volatile boolean cancelled;
        private Ticket(Context context) { this.context = context; }
        public void check() throws Failure {
            if (cancelled || Thread.currentThread().isInterrupted()) throw new Failure("BACKUP_CONTEXT_CHANGED");
        }
    }
    private Context context = new Context("", "", -1, 0);
    private Ticket active;

    public synchronized boolean setContext(String backend, String ownerId, long epoch, boolean debug) throws Failure {
        if (epoch < 0 || epoch > MAX_EPOCH || ownerId == null || !(ownerId.isEmpty() || ownerId.matches("[a-f0-9]{32}"))) throw new Failure("BACKUP_CONTEXT_INVALID");
        String origin = backend != null && backend.isEmpty() && ownerId.isEmpty() ? "" : origin(backend, debug);
        if (epoch == context.epoch) {
            if (!origin.equals(context.backend) || !ownerId.equals(context.ownerId)) throw new Failure("BACKUP_CONTEXT_CHANGED");
            return false;
        }
        if (epoch < context.epoch) throw new Failure("BACKUP_CONTEXT_CHANGED");
        if (active != null) active.cancelled = true;
        active = null;
        context = new Context(origin, ownerId, epoch, context.generation + 1);
        return true;
    }
    public synchronized Context current() { return context; }
    /** Native main-frame navigation only: JS auth epochs restart in a new document. */
    public synchronized void resetForNavigation() {
        if (active != null) active.cancelled = true;
        active = null;
        context = new Context("", "", -1, context.generation + 1);
    }
    public synchronized Context require(long epoch) throws Failure {
        if (epoch != context.epoch || context.ownerId.isEmpty() || context.backend.isEmpty()) throw new Failure("BACKUP_CONTEXT_CHANGED");
        return context;
    }
    public synchronized Ticket begin(long epoch) throws Failure {
        require(epoch);
        if (active != null) throw new Failure("BACKUP_TRANSFER_BUSY");
        active = new Ticket(context); return active;
    }
    public synchronized boolean valid(Ticket ticket) {
        return ticket != null && !ticket.cancelled && ticket.context.generation == context.generation && active == ticket;
    }
    public synchronized void end(Ticket ticket) { if (active == ticket) active = null; }
    public synchronized void cancel(Ticket ticket) {
        if (ticket != null) ticket.cancelled = true;
        if (active == ticket) active = null;
    }
    public static String origin(String value, boolean debug) throws Failure {
        if (value == null || value.isEmpty() || value.length() > 2048 || value.indexOf('\\') >= 0 || !value.equals(value.trim())) throw new Failure("BACKUP_BACKEND_INVALID");
        try {
            URI uri = new URI(value);
            String scheme = uri.getScheme(), host = uri.getHost();
            if (host == null || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null || uri.getPort() == 0 || uri.getPort() > 65535 || uri.getPort() < -1) throw new Failure("BACKUP_BACKEND_INVALID");
            String path = uri.getRawPath();
            if (!(path == null || path.isEmpty() || "/".equals(path))) throw new Failure("BACKUP_BACKEND_INVALID");
            if (!("https".equals(scheme) || (debug && "http".equals(scheme) && ("localhost".equals(host) || "127.0.0.1".equals(host) || "10.0.2.2".equals(host))))) throw new Failure("BACKUP_BACKEND_INVALID");
            // Round-tripping rejects malformed authority spellings rather than
            // allowing URL parsing to reinterpret an apparent loopback origin.
            URI canonical = new URI(scheme, null, host, uri.getPort(), null, null, null);
            if (!canonical.getRawAuthority().equals(uri.getRawAuthority())) throw new Failure("BACKUP_BACKEND_INVALID");
            return canonical.toASCIIString();
        } catch (URISyntaxException error) { throw new Failure("BACKUP_BACKEND_INVALID"); }
    }
    public static URI endpoint(Context context, String suffix) throws Failure {
        if (!suffix.equals("prepare") && !suffix.matches("[a-f0-9]{32}/download")) throw new Failure("BACKUP_OPERATION_INVALID");
        return URI.create(context.backend + "/api/reader/v1/backups/" + suffix);
    }
    public static boolean sameDocument(String claimed, Object current) {
        return claimed != null && claimed.matches("[a-f0-9]{32}") && claimed.equals(current);
    }
    public static final class ReadingExport {
        public final String operationId, format, sha256, fileName, mime;
        public final long size;
        private ReadingExport(String id, String format, long size, String hash, String name) {
            this.operationId = id; this.format = format; this.size = size; this.sha256 = hash; this.fileName = name;
            this.mime = "txt".equals(format) ? "text/plain" : "application/epub+zip";
        }
        public void checkResponse(String type, String encoding, long length) throws Failure {
            if (type == null || !mime.equals(type.split(";", 2)[0].trim().toLowerCase(java.util.Locale.ROOT))
                || (encoding != null && !"identity".equalsIgnoreCase(encoding)) || (length >= 0 && length != size)) throw new Failure("EXPORT_FILE_INVALID");
        }
    }
    public static ReadingExport readingExport(String id, String format, Object size, String hash, String name) throws Failure {
        if (id == null || !id.matches("[a-f0-9]{32}") || !("txt".equals(format) || "epub".equals(format))
            || !(size instanceof Number) || hash == null || !hash.matches("[a-f0-9]{64}") || name == null || name.isEmpty()
            || name.startsWith(".") || name.endsWith(" ") || name.chars().anyMatch(c -> c < 32 || c == 127 || "\\/:*?\"<>|".indexOf(c) >= 0)
            || !name.endsWith("." + format)) throw new Failure("EXPORT_FILE_INVALID");
        double bytes = ((Number) size).doubleValue();
        if (!Double.isFinite(bytes) || bytes <= 0 || bytes > MAX_READING_BYTES || Math.rint(bytes) != bytes) throw new Failure("EXPORT_FILE_INVALID");
        try {
            ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(name));
            if (encoded.remaining() > 512) throw new Failure("EXPORT_FILE_INVALID");
        } catch (CharacterCodingException error) { throw new Failure("EXPORT_FILE_INVALID"); }
        return new ReadingExport(id, format, (long) bytes, hash, name);
    }
    public static URI readingEndpoint(Context context, String id) throws Failure {
        if (id == null || !id.matches("[a-f0-9]{32}")) throw new Failure("EXPORT_FILE_INVALID");
        return URI.create(context.backend + "/api/reader/v1/exports/" + id + "/download");
    }
    public static String requestId(String value) throws Failure {
        if (value == null || !value.matches("[A-Za-z0-9._:-]{1,160}")) throw new Failure("BACKUP_REQUEST_INVALID");
        return value;
    }
    public static String accessToken(String value) throws Failure {
        if (value == null || value.length() > 4096 || !value.matches("[A-Za-z0-9._~+/-]+={0,2}")) throw new Failure("BACKUP_AUTH_REQUIRED");
        return value;
    }
    public static byte[] password(String value) throws Failure {
        if (value == null || value.isEmpty() || value.length() > 1024) throw new Failure("BACKUP_PASSWORD_REQUIRED");
        try {
            ByteBuffer bytes = StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(value));
            if (bytes.remaining() > 1024) throw new Failure("BACKUP_PASSWORD_REQUIRED");
            byte[] output = new byte[bytes.remaining()]; bytes.get(output); return output;
        } catch (CharacterCodingException error) { throw new Failure("BACKUP_PASSWORD_REQUIRED"); }
    }
    public static final class Header {
        public final byte[] bytes;
        public final long totalBytes;
        private Header(byte[] bytes, long totalBytes) { this.bytes = bytes; this.totalBytes = totalBytes; }
    }
    public static Header inspectHeader(byte[] bytes, long knownSize) throws Failure {
        if (bytes == null || bytes.length != HEADER_BYTES) throw new Failure("BACKUP_HEADER_INVALID");
        ByteBuffer data = ByteBuffer.wrap(bytes);
        byte[] magic = new byte[8]; data.get(magic);
        if (!java.util.Arrays.equals(magic, "SRBKUP01".getBytes(StandardCharsets.US_ASCII))) throw new Failure("BACKUP_FORMAT_INVALID");
        if (Short.toUnsignedInt(data.getShort()) != 1) throw new Failure("BACKUP_VERSION_UNSUPPORTED");
        if (data.getInt() != 32768 || data.getInt() != 8 || data.getInt() != 1) throw new Failure("BACKUP_KDF_UNSUPPORTED");
        data.position(50); long ciphertext = data.getLong();
        if (ciphertext < 16 || ciphertext > MAX_CIPHERTEXT_BYTES) throw new Failure("BACKUP_SIZE_LIMIT");
        long total = HEADER_BYTES + ciphertext;
        if (knownSize >= 0 && knownSize != total) throw new Failure("BACKUP_SOURCE_CHANGED");
        return new Header(bytes.clone(), total);
    }
    public ReaderBackupTransferPolicy() {}
}
