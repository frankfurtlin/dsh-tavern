package app.storyreader.personal;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.security.MessageDigest;

/** One-pass multipart/cipher copies with a fixed 64 KiB memory ceiling. */
public final class ReaderMultipartStream implements AutoCloseable {
    public interface Cancellation { void check() throws IOException; }
    public interface Progress { void bytes(long bytes) throws IOException; }
    private byte[] prefix;
    private final byte[] suffix;
    private final long fileLength;
    private final long contentLength;
    private boolean consumed;

    public ReaderMultipartStream(String boundary, byte[] password, long fileLength) throws IOException {
        if (boundary == null || !boundary.matches("readerbackup[a-f0-9]{32}") || password == null || password.length == 0 || password.length > 1024 || fileLength < 74 || fileLength > ReaderBackupTransferPolicy.MAX_FILE_BYTES) throw new ReaderBackupTransferPolicy.Failure("BACKUP_REQUEST_INVALID");
        this.fileLength = fileLength;
        byte[] start = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\n").getBytes(StandardCharsets.US_ASCII);
        byte[] end = ("\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"private.srbackup\"\r\nContent-Type: application/octet-stream\r\n\r\n").getBytes(StandardCharsets.US_ASCII);
        prefix = new byte[start.length + password.length + end.length];
        System.arraycopy(start, 0, prefix, 0, start.length);
        System.arraycopy(password, 0, prefix, start.length, password.length);
        System.arraycopy(end, 0, prefix, start.length + password.length, end.length);
        suffix = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.US_ASCII);
        contentLength = prefix.length + fileLength + suffix.length;
    }
    public long contentLength() { return contentLength; }
    public void writeTo(InputStream source, OutputStream target, Cancellation cancellation, Progress progress) throws IOException {
        if (prefix == null || consumed) throw new ReaderBackupTransferPolicy.Failure("BACKUP_REQUEST_INVALID");
        consumed = true;
        try { cancellation.check(); target.write(prefix); } finally { Arrays.fill(prefix, (byte) 0); }
        copyExact(source, target, fileLength, cancellation, progress);
        cancellation.check(); target.write(suffix); target.flush();
    }
    public static byte[] readHeader(InputStream source, Cancellation cancellation) throws IOException {
        byte[] header = new byte[ReaderBackupTransferPolicy.HEADER_BYTES]; int offset = 0;
        while (offset < header.length) {
            cancellation.check(); int read = source.read(header, offset, header.length - offset);
            if (read < 0) throw new ReaderBackupTransferPolicy.Failure("BACKUP_HEADER_INVALID");
            if (read == 0) {
                int one = source.read(); if (one < 0) throw new ReaderBackupTransferPolicy.Failure("BACKUP_HEADER_INVALID");
                header[offset++] = (byte) one;
            } else offset += read;
        }
        return header;
    }
    public static long copyExact(InputStream source, OutputStream target, long length, Cancellation cancellation, Progress progress) throws IOException {
        if (length < 0 || length > ReaderBackupTransferPolicy.MAX_FILE_BYTES) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SIZE_LIMIT");
        byte[] buffer = new byte[ReaderBackupTransferPolicy.CHUNK_BYTES]; long copied = 0;
        try {
            while (copied < length) {
                cancellation.check();
                int read = source.read(buffer, 0, (int) Math.min(buffer.length, length - copied));
                if (read < 0) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SOURCE_CHANGED");
                if (read == 0) {
                    int one = source.read(); if (one < 0) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SOURCE_CHANGED");
                    buffer[0] = (byte) one; read = 1;
                }
                cancellation.check(); target.write(buffer, 0, read); copied += read; progress.bytes(copied);
            }
            cancellation.check();
            if (source.read() != -1) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SOURCE_CHANGED");
            return copied;
        } finally { Arrays.fill(buffer, (byte) 0); }
    }
    /** Public export: exact EOF and digest before a system save picker is opened. */
    public static long copyVerified(InputStream source, OutputStream target, long length, String hash, Cancellation cancellation, Progress progress) throws IOException {
        if (length <= 0 || length > ReaderBackupTransferPolicy.MAX_READING_BYTES || hash == null || !hash.matches("[a-f0-9]{64}")) throw new ReaderBackupTransferPolicy.Failure("EXPORT_FILE_INVALID");
        final MessageDigest digest;
        try { digest = MessageDigest.getInstance("SHA-256"); } catch (java.security.NoSuchAlgorithmException error) { throw new IOException(error); }
        InputStream checked = new java.io.FilterInputStream(source) {
            @Override public int read(byte[] buffer, int offset, int count) throws IOException {
                int read = in.read(buffer, offset, count); if (read > 0) digest.update(buffer, offset, read); return read;
            }
            @Override public int read() throws IOException { int value = in.read(); if (value >= 0) digest.update((byte) value); return value; }
        };
        long copied;
        try { copied = copyExact(checked, target, length, cancellation, progress); }
        catch (ReaderBackupTransferPolicy.Failure error) {
            if ("BACKUP_SOURCE_CHANGED".equals(error.code)) throw new ReaderBackupTransferPolicy.Failure("EXPORT_FILE_INVALID");
            throw error;
        }
        byte[] expected = new byte[32]; for (int i = 0; i < 32; i++) expected[i] = (byte) Integer.parseInt(hash.substring(i * 2, i * 2 + 2), 16);
        if (!MessageDigest.isEqual(expected, digest.digest())) throw new ReaderBackupTransferPolicy.Failure("EXPORT_FILE_INVALID");
        return copied;
    }
    @Override public void close() { if (prefix != null) { Arrays.fill(prefix, (byte) 0); prefix = null; } }
}
