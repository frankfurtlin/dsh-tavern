package app.storyreader.personal;

import static org.junit.Assert.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.Test;

/** Public synthetic fixtures only; no Android picker, network or provider. */
public class ReaderBackupTransferTest {
    private static final String A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private static final ReaderMultipartStream.Cancellation OK = () -> {};
    private static final ReaderMultipartStream.Progress IGNORE = bytes -> {};
    private interface Checked { void run() throws Exception; }
    private static void failure(String code, Checked action) throws Exception {
        try { action.run(); fail("expected " + code); }
        catch (ReaderBackupTransferPolicy.Failure error) { assertEquals(code, error.code); }
    }
    private static byte[] header(long ciphertext) {
        ByteBuffer bytes = ByteBuffer.allocate(58);
        bytes.put("SRBKUP01".getBytes(StandardCharsets.US_ASCII)).putShort((short) 1).putInt(32768).putInt(8).putInt(1);
        bytes.position(50); bytes.putLong(ciphertext); return bytes.array();
    }
    private static byte[] file(int ciphertext) {
        byte[] result = new byte[58 + ciphertext]; System.arraycopy(header(ciphertext), 0, result, 0, 58);
        for (int i = 58; i < result.length; i++) result[i] = (byte) i;
        return result;
    }
    @Test public void productionHttpsOriginAndExactEndpoints() throws Exception {
        assertEquals("https://reader.example:9443", ReaderBackupTransferPolicy.origin("https://reader.example:9443/", false));
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); p.setContext("https://reader.example/", A, 1, false);
        assertEquals("https://reader.example/api/reader/v1/backups/" + B + "/download", ReaderBackupTransferPolicy.endpoint(p.current(), B + "/download").toString());
        failure("BACKUP_OPERATION_INVALID", () -> ReaderBackupTransferPolicy.endpoint(p.current(), "../secret"));
        failure("BACKUP_BACKEND_INVALID", () -> ReaderBackupTransferPolicy.origin("http://localhost", false));
    }
    @Test public void hostileOriginsNeverReinterpretCredentialsOrLocalhost() throws Exception {
        String[] bad = {"https://user@reader.example", "https://reader.example?token=x", "https://reader.example#x", "https://reader.example/api", "https://reader.example/%2e%2e", "https://reader.example\\evil", "http://localhost.evil", "http://127.1", "http://2130706433", "http://127.0.0.01", "http://LOCALHOST", "http://10.0.2.20", "http://reader.example", "https://reader.example:", "https://reader.example:0", "https://reader.example:65536", " https://reader.example"};
        for (String origin : bad) failure("BACKUP_BACKEND_INVALID", () -> ReaderBackupTransferPolicy.origin(origin, true));
        for (String host : new String[]{"localhost", "127.0.0.1", "10.0.2.2"}) assertEquals("http://" + host + ":18899", ReaderBackupTransferPolicy.origin("http://" + host + ":18899/", true));
    }
    @Test public void sameContextIsIdempotentAndSameEpochCannotSwitchAccounts() throws Exception {
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); assertTrue(p.setContext("https://reader.example", A, 5, false));
        ReaderBackupTransferPolicy.Ticket live = p.begin(5);
        assertFalse(p.setContext("https://reader.example/", A, 5, false)); assertTrue(p.valid(live));
        failure("BACKUP_CONTEXT_CHANGED", () -> p.setContext("https://reader.example", B, 5, false)); assertTrue(p.valid(live));
        failure("BACKUP_CONTEXT_CHANGED", () -> p.setContext("https://reader.example", A, 4, false)); assertTrue(p.valid(live));
    }
    @Test public void accountSwitchInvalidatesOldCompletionsAndOldEndCannotUnlockNewJob() throws Exception {
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); p.setContext("https://reader.example", A, 1, false);
        ReaderBackupTransferPolicy.Ticket old = p.begin(1); p.setContext("https://reader.example", B, 2, false);
        assertFalse(p.valid(old)); failure("BACKUP_CONTEXT_CHANGED", old::check);
        ReaderBackupTransferPolicy.Ticket current = p.begin(2); p.end(old);
        failure("BACKUP_TRANSFER_BUSY", () -> p.begin(2)); assertTrue(p.valid(current));
        p.cancel(current); assertFalse(p.valid(current)); assertTrue(p.valid(p.begin(2)));
    }
    @Test public void clearedContextAndInvalidOwnersCannotStartAFileOperation() throws Exception {
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); p.setContext("", "", 0, false);
        failure("BACKUP_CONTEXT_CHANGED", () -> p.begin(0));
        failure("BACKUP_CONTEXT_INVALID", () -> p.setContext("https://reader.example", "Alice", 1, false));
        failure("BACKUP_CONTEXT_INVALID", () -> p.setContext("https://reader.example", A, ReaderBackupTransferPolicy.MAX_EPOCH + 1, false));
    }
    @Test public void nativeNavigationAllowsFreshJsEpochWithoutRevivingOldCompletion() throws Exception {
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); p.setContext("https://reader.example", A, 17, false);
        ReaderBackupTransferPolicy.Ticket old = p.begin(17); long generation = p.current().generation;
        p.resetForNavigation(); assertTrue(p.current().generation > generation); assertFalse(p.valid(old));
        failure("BACKUP_CONTEXT_CHANGED", old::check); failure("BACKUP_CONTEXT_CHANGED", () -> p.begin(0));
        p.setContext("https://reader.example", A, 0, false); ReaderBackupTransferPolicy.Ticket current = p.begin(0);
        assertTrue(p.valid(current)); p.end(old); assertTrue(p.valid(current));
        failure("BACKUP_CONTEXT_CHANGED", () -> p.setContext("https://reader.example", B, 0, false));
    }
    @Test public void passwordEncodingAndHeaderInjectionLimits() throws Exception {
        assertArrayEquals("密碼🙂".getBytes(StandardCharsets.UTF_8), ReaderBackupTransferPolicy.password("密碼🙂"));
        failure("BACKUP_PASSWORD_REQUIRED", () -> ReaderBackupTransferPolicy.password("\ud800"));
        failure("BACKUP_PASSWORD_REQUIRED", () -> ReaderBackupTransferPolicy.password(new String(new char[1025]).replace('\0', 'a')));
        failure("BACKUP_PASSWORD_REQUIRED", () -> ReaderBackupTransferPolicy.password(new String(new char[400]).replace('\0', '中')));
        failure("BACKUP_REQUEST_INVALID", () -> ReaderBackupTransferPolicy.requestId("abc\r\nAuthorization: x"));
        failure("BACKUP_AUTH_REQUIRED", () -> ReaderBackupTransferPolicy.accessToken("abc\r\nx"));
    }
    @Test public void headerChecksCipherSizeKdfVersionAndProviderLength() throws Exception {
        assertEquals(74, ReaderBackupTransferPolicy.inspectHeader(header(16), 74).totalBytes);
        assertEquals(58 + ReaderBackupTransferPolicy.MAX_CIPHERTEXT_BYTES, ReaderBackupTransferPolicy.inspectHeader(header(ReaderBackupTransferPolicy.MAX_CIPHERTEXT_BYTES), -1).totalBytes);
        failure("BACKUP_SOURCE_CHANGED", () -> ReaderBackupTransferPolicy.inspectHeader(header(16), 75));
        for (long bad : new long[]{-1, 0, 15, ReaderBackupTransferPolicy.MAX_CIPHERTEXT_BYTES + 1}) failure("BACKUP_SIZE_LIMIT", () -> ReaderBackupTransferPolicy.inspectHeader(header(bad), -1));
        byte[] version = header(16); version[9] = 2; failure("BACKUP_VERSION_UNSUPPORTED", () -> ReaderBackupTransferPolicy.inspectHeader(version, -1));
        byte[] kdf = header(16); kdf[13] = 1; failure("BACKUP_KDF_UNSUPPORTED", () -> ReaderBackupTransferPolicy.inspectHeader(kdf, -1));
        byte[] magic = header(16); magic[0] = 0; failure("BACKUP_FORMAT_INVALID", () -> ReaderBackupTransferPolicy.inspectHeader(magic, -1));
    }
    @Test public void multipartPreservesCipherAndUtf8PasswordWithExactContentLength() throws Exception {
        String boundary = "readerbackup" + A; byte[] input = file(123), password = "密碼🙂".getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (ReaderMultipartStream body = new ReaderMultipartStream(boundary, password, input.length)) {
            long length = body.contentLength(); Arrays.fill(password, (byte) 0);
            body.writeTo(new ByteArrayInputStream(input), out, OK, IGNORE); assertEquals(length, out.size());
            ByteArrayOutputStream expected = new ByteArrayOutputStream();
            expected.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\n密碼🙂\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"private.srbackup\"\r\nContent-Type: application/octet-stream\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            expected.write(input); expected.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.US_ASCII)); assertArrayEquals(expected.toByteArray(), out.toByteArray());
        }
    }
    @Test public void multipartCannotReplayAfterSuccessOrCancellation() throws Exception {
        ReaderMultipartStream body = new ReaderMultipartStream("readerbackup" + A, new byte[]{1}, 74);
        body.writeTo(new ByteArrayInputStream(file(16)), new ByteArrayOutputStream(), OK, IGNORE);
        failure("BACKUP_REQUEST_INVALID", () -> body.writeTo(new ByteArrayInputStream(file(16)), new ByteArrayOutputStream(), OK, IGNORE)); body.close();
        ReaderMultipartStream cancelled = new ReaderMultipartStream("readerbackup" + A, new byte[]{1}, 74);
        failure("BACKUP_CONTEXT_CHANGED", () -> cancelled.writeTo(new ByteArrayInputStream(file(16)), new ByteArrayOutputStream(), () -> { throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_CHANGED"); }, IGNORE));
        failure("BACKUP_REQUEST_INVALID", () -> cancelled.writeTo(new ByteArrayInputStream(file(16)), new ByteArrayOutputStream(), OK, IGNORE)); cancelled.close();
    }
    @Test public void exactCopyRejectsShortOrLongStreamsAndNeverReportsFullSuccess() throws Exception {
        failure("BACKUP_SOURCE_CHANGED", () -> ReaderMultipartStream.copyExact(new ByteArrayInputStream(new byte[9]), new ByteArrayOutputStream(), 10, OK, IGNORE));
        failure("BACKUP_SOURCE_CHANGED", () -> ReaderMultipartStream.copyExact(new ByteArrayInputStream(new byte[11]), new ByteArrayOutputStream(), 10, OK, IGNORE));
        failure("BACKUP_HEADER_INVALID", () -> ReaderMultipartStream.readHeader(new ByteArrayInputStream(new byte[57]), OK));
    }
    @Test public void zeroLengthReadsStillProgressWithoutBusyLoop() throws Exception {
        byte[] data = file(16); InputStream awkward = new ByteArrayInputStream(data) { @Override public synchronized int read(byte[] b, int o, int n) { return 0; } };
        ByteArrayOutputStream target = new ByteArrayOutputStream(); assertEquals(data.length, ReaderMultipartStream.copyExact(awkward, target, data.length, OK, IGNORE)); assertArrayEquals(data, target.toByteArray());
        InputStream headerSource = new ByteArrayInputStream(header(16)) { @Override public synchronized int read(byte[] b, int o, int n) { return 0; } };
        assertArrayEquals(header(16), ReaderMultipartStream.readHeader(headerSource, OK));
    }
    @Test public void contextCancellationAfterReadPreventsWritingOldAccountBytes() throws Exception {
        ReaderBackupTransferPolicy p = new ReaderBackupTransferPolicy(); p.setContext("https://reader.example", A, 1, false); ReaderBackupTransferPolicy.Ticket ticket = p.begin(1);
        InputStream source = new ByteArrayInputStream(new byte[128]) { @Override public synchronized int read(byte[] b, int o, int n) { int count = super.read(b, o, n); p.cancel(ticket); return count; } };
        ByteArrayOutputStream target = new ByteArrayOutputStream();
        failure("BACKUP_CONTEXT_CHANGED", () -> ReaderMultipartStream.copyExact(source, target, 128, ticket::check, IGNORE)); assertEquals(0, target.size());
    }
    @Test public void outputWriteFailurePropagatesAndDoesNotAutomaticallyReplayMultipart() throws Exception {
        AtomicLong attempts = new AtomicLong(); OutputStream external = new OutputStream() {
            @Override public void write(int b) throws IOException { attempts.incrementAndGet(); throw new IOException("synthetic provider write failure"); }
        };
        try (ReaderMultipartStream body = new ReaderMultipartStream("readerbackup" + A, new byte[]{1}, 74)) {
            try { body.writeTo(new ByteArrayInputStream(file(16)), external, OK, IGNORE); fail(); }
            catch (IOException expected) { assertEquals("synthetic provider write failure", expected.getMessage()); }
            failure("BACKUP_REQUEST_INVALID", () -> body.writeTo(new ByteArrayInputStream(file(16)), external, OK, IGNORE));
            assertEquals(1, attempts.get());
        }
    }
    @Test public void actual128MiBFileStreamsWithAtMost64KiBReadsAndMonotoneProgress() throws Exception {
        File fixture = Files.createTempFile("reader-backup-owned-test-", ".cipher").toFile(); long total = 128L * 1024 * 1024;
        try {
            byte[] chunk = new byte[65536]; Arrays.fill(chunk, (byte) 0x5a);
            try (FileOutputStream file = new FileOutputStream(fixture)) { for (long n = 0; n < total; n += chunk.length) file.write(chunk); }
            AtomicLong maxRead = new AtomicLong(), written = new AtomicLong(), lastProgress = new AtomicLong();
            try (InputStream source = new FilterInputStream(new FileInputStream(fixture)) {
                @Override public int read(byte[] b, int o, int n) throws IOException { maxRead.accumulateAndGet(n, Math::max); return super.read(b, o, n); }
            }) {
                OutputStream sink = new OutputStream() { @Override public void write(int b) { written.incrementAndGet(); } @Override public void write(byte[] b, int o, int n) { written.addAndGet(n); } };
                assertEquals(total, ReaderMultipartStream.copyExact(source, sink, total, OK, bytes -> { assertTrue(bytes > lastProgress.get()); lastProgress.set(bytes); }));
            }
            assertEquals(total, written.get()); assertEquals(total, lastProgress.get()); assertEquals(65536, maxRead.get());
        } finally { assertTrue(fixture.delete()); }
    }
}
