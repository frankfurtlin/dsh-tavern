package app.storyreader.personal;

import java.util.Arrays;

/** Executable probes of native state, without an emulator or a substitute audio engine. */
public final class ReaderSpeechQueueProbe {
    private static int cases;
    private static void check(boolean result, String message) { if (!result) throw new AssertionError(message); }
    private static ReaderSpeechQueue queue(boolean endChapter) {
        ReaderSpeechQueue q = new ReaderSpeechQueue();
        q.load("owned-scope", "原创样本", Arrays.asList(
            new ReaderSpeechQueue.Segment("p1", "甲😀乙。", "c1", "第一章", "{\"blockId\":\"b1\"}"),
            new ReaderSpeechQueue.Segment("p2", "夜色深了。", "c1", "第一章", "{\"blockId\":\"b2\"}"),
            new ReaderSpeechQueue.Segment("p3", "船靠岸了。", "c2", "第二章", "{\"blockId\":\"b3\"}")), null, 1, "", endChapter);
        return q;
    }
    public static void main(String[] args) {
        ReaderSpeechQueue q = queue(false); q.play();
        ReaderSpeechQueue.Utterance first = q.beginUtterance(2);
        check(first.text.equals("甲"), "a chunk may not cut an emoji surrogate pair");
        q.started(first.ticket); check(!q.finished(first.ticket), "a chunk is not a completed paragraph");
        check(q.lastCompleted() == null && q.index == 0, "chunk completion cannot advance the paragraph marker");
        ReaderSpeechQueue.Utterance emoji = q.beginUtterance(2);
        check(emoji.text.equals("😀") && emoji.startUtf16 == 1, "native offsets use UTF16");
        q.started(emoji.ticket); q.finished(emoji.ticket);
        ReaderSpeechQueue.Utterance end = q.beginUtterance(2); q.started(end.ticket);
        check(q.finished(end.ticket) && q.index == 1 && q.lastCompleted().id.equals("p1"), "only full actual completion advances marker"); cases++;

        q = queue(false); q.play(); first = q.beginUtterance(100); q.pause("headphones-disconnected");
        check(!q.finished(first.ticket) && q.index == 0 && q.lastCompleted() == null, "late completion after interruption cannot mark prose read");
        check(q.beginUtterance(100) == null, "focus gain must not resume a paused queue"); cases++;

        q.play(); first = q.beginUtterance(100); q.pause("interrupted"); q.play();
        ReaderSpeechQueue.Utterance replay = q.beginUtterance(100);
        check(replay.text.equals("甲😀乙。") && replay.startUtf16 == 0 && !replay.ticket.equals(first.ticket), "resume restarts at most the current paragraph");
        check(!q.started(first.ticket), "stale engine callbacks are ignored"); cases++;

        q = queue(true); q.play(); first = q.beginUtterance(100); q.finished(first.ticket);
        first = q.beginUtterance(100); q.finished(first.ticket);
        check(q.status.equals("paused") && q.reason.equals("chapter-ended") && q.index == 2 && q.lastCompleted().id.equals("p2"), "chapter timer stops before next chapter is spoken"); cases++;

        q = queue(false); q.play(); first = q.beginUtterance(100); q.move(1);
        check(!q.finished(first.ticket) && q.lastCompleted() == null && q.current().id.equals("p2"), "manual jump is not proof of completed playback"); cases++;

        q = queue(false); boolean rejected = false;
        try { q.load("other", "非法样本", Arrays.asList(new ReaderSpeechQueue.Segment("bad", "文字", "c", "", "{}")), null, Double.NaN, "", false); }
        catch (IllegalArgumentException expected) { rejected = true; }
        check(rejected && q.scopeKey.equals("owned-scope") && q.count() == 3, "invalid load must not partially replace existing scope"); cases++;

        q.play(); first = q.beginUtterance(100); q.clear();
        check(q.scopeKey.isEmpty() && q.count() == 0 && !q.finished(first.ticket), "logout clears old queue and invalidates pending callback"); cases++;
        for (int minutes : new int[]{0, 5, 15, 30, 60}) ReaderSpeechQueue.checkSleep(minutes);
        rejected = false; try { ReaderSpeechQueue.checkSleep(-1); } catch (IllegalArgumentException expected) { rejected = true; }
        check(rejected, "invalid timer is rejected before mutation"); cases++;
        ReaderSpeechQueue.checkRate(.75); ReaderSpeechQueue.checkRate(2);
        for (double speed : new double[]{.749, 2.001, Double.POSITIVE_INFINITY, Double.NaN}) {
            rejected = false; try { ReaderSpeechQueue.checkRate(speed); } catch (IllegalArgumentException expected) { rejected = true; }
            check(rejected, "speed must stay within the finite supported interval");
        }
        cases++;
        System.out.println("{\"cases\":" + cases + ",\"failures\":0,\"type\":\"actual-jvm-native-queue\",\"audioPlaybackCertified\":false}");
    }
}
