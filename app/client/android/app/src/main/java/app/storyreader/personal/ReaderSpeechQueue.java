package app.storyreader.personal;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

/** Native paragraph queue. Only actual engine completion advances the saved cursor. */
final class ReaderSpeechQueue {
    static final class Segment {
        final String id, text, chapterId, chapterTitle, anchor;
        Segment(String id, String text, String chapterId, String chapterTitle, String anchor) {
            this.id = id; this.text = text; this.chapterId = chapterId;
            this.chapterTitle = chapterTitle; this.anchor = anchor;
        }
    }
    static final class Utterance {
        final String ticket, text;
        final int startUtf16;
        Utterance(String ticket, String text, int start) {
            this.ticket = ticket; this.text = text; this.startUtf16 = start;
        }
    }
    private final ArrayList<Segment> segments = new ArrayList<>();
    String scopeKey = "", title = "", status = "idle", reason = "", voiceId = "";
    double rate = 1;
    boolean stopAtChapterEnd;
    int index, lastCompletedIndex = -1;
    long generation, eventVersion;
    private long utteranceOrdinal;
    private int offset, activeEnd;
    private String activeTicket;

    void load(String scope, String storyTitle, List<Segment> values, String fromId,
              double speed, String voice, boolean endChapter) {
        if (scope == null || scope.isEmpty() || scope.length() > 512 || storyTitle == null || storyTitle.length() > 500)
            throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        checkRate(speed);
        if (voice != null && voice.length() > 500) throw new IllegalArgumentException("INVALID_SPEECH_VOICE");
        if (values == null || values.isEmpty() || values.size() > 20000)
            throw new IllegalArgumentException("INVALID_SPEECH_QUEUE");
        int size = 0, anchorSize = 0, selected = fromId == null || fromId.isEmpty() ? 0 : -1;
        HashSet<String> ids = new HashSet<>();
        for (int i = 0; i < values.size(); i++) {
            Segment s = values.get(i);
            if (s == null || s.id == null || s.id.isEmpty() || s.id.length() > 200 || !ids.add(s.id)
                || s.text == null || s.text.trim().isEmpty() || s.text.length() > 200000 || !validUtf16(s.text)
                || s.chapterId == null || s.chapterId.isEmpty() || s.chapterId.length() > 200
                || s.chapterTitle == null || s.chapterTitle.length() > 500
                || s.anchor == null || s.anchor.length() > 8192)
                throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
            size += s.text.length();
            anchorSize += s.anchor.length();
            if (size > 2000000 || anchorSize > 8000000) throw new IllegalArgumentException("SPEECH_QUEUE_TOO_LARGE");
            if (s.id.equals(fromId)) selected = i;
        }
        if (selected < 0) throw new IllegalArgumentException("SPEECH_POSITION_NOT_FOUND");
        invalidate(); segments.clear(); segments.addAll(values);
        scopeKey = scope; title = storyTitle; index = selected; lastCompletedIndex = -1;
        rate = speed; voiceId = voice == null ? "" : voice; stopAtChapterEnd = endChapter;
        status = "ready"; reason = ""; eventVersion++;
    }
    static void checkRate(double speed) {
        if (!Double.isFinite(speed) || speed < .75 || speed > 2)
            throw new IllegalArgumentException("INVALID_SPEECH_RATE");
    }
    static boolean validUtf16(String value) {
        for (int index = 0; index < value.length(); index++) {
            char unit = value.charAt(index);
            if (Character.isHighSurrogate(unit)) {
                if (++index >= value.length() || !Character.isLowSurrogate(value.charAt(index))) return false;
            } else if (Character.isLowSurrogate(unit)) return false;
        }
        return true;
    }
    static void checkSleep(int minutes) {
        if (minutes != 0 && minutes != 5 && minutes != 15 && minutes != 30 && minutes != 60)
            throw new IllegalArgumentException("INVALID_SLEEP_TIMER");
    }
    void play() {
        if (segments.isEmpty()) throw new IllegalStateException("SPEECH_QUEUE_EMPTY");
        if (index >= segments.size()) throw new IllegalStateException("SPEECH_ENDED");
        invalidate(); status = "starting"; reason = ""; eventVersion++;
    }
    boolean wantsPlayback() { return "starting".equals(status) || "playing".equals(status); }
    Utterance beginUtterance(int limit) {
        if (!wantsPlayback() || activeTicket != null || current() == null) return null;
        if (limit < 2) throw new IllegalArgumentException("INVALID_SPEECH_CHUNK_SIZE");
        String text = current().text;
        int end = Math.min(text.length(), offset + limit);
        if (end < text.length() && Character.isHighSurrogate(text.charAt(end - 1)) && Character.isLowSurrogate(text.charAt(end))) end--;
        activeEnd = end;
        activeTicket = generation + ":" + (++utteranceOrdinal) + ":" + index + ":" + offset;
        return new Utterance(activeTicket, text.substring(offset, end), offset);
    }
    boolean started(String ticket) {
        if (!accepts(ticket)) return false;
        status = "playing"; eventVersion++; return true;
    }
    boolean accepts(String ticket) { return wantsPlayback() && ticket != null && ticket.equals(activeTicket); }
    boolean finished(String ticket) {
        if (!accepts(ticket)) return false;
        offset = activeEnd; activeTicket = null;
        if (offset < current().text.length()) return false;
        String chapter = current().chapterId;
        lastCompletedIndex = index; index++; offset = 0; eventVersion++;
        if (index >= segments.size()) { status = "ended"; reason = "completed"; }
        else if (stopAtChapterEnd && !chapter.equals(current().chapterId)) { status = "paused"; reason = "chapter-ended"; }
        return true;
    }
    void pause(String why) {
        invalidate(); status = segments.isEmpty() ? "idle" : "paused"; reason = why; eventVersion++;
    }
    void error(String code) { pause(code); status = "error"; }
    void move(int direction) {
        if (segments.isEmpty()) throw new IllegalStateException("SPEECH_QUEUE_EMPTY");
        int next = Math.max(0, Math.min(segments.size() - 1, index + direction));
        invalidate(); index = next; status = "paused"; reason = "selected"; eventVersion++;
    }
    void clear() {
        invalidate(); segments.clear(); scopeKey = ""; title = ""; index = 0; lastCompletedIndex = -1;
        status = "idle"; reason = "stopped"; eventVersion++;
    }
    Segment current() { return index < segments.size() ? segments.get(index) : null; }
    Segment lastCompleted() { return lastCompletedIndex < 0 ? null : segments.get(lastCompletedIndex); }
    int count() { return segments.size(); }
    private void invalidate() { generation++; activeTicket = null; offset = 0; activeEnd = 0; }
}
