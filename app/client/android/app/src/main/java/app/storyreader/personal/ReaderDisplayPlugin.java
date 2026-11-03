package app.storyreader.personal;

import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Build;
import android.view.View;
import android.view.ViewTreeObserver;
import android.view.WindowMetrics;
import android.webkit.WebView;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.WebViewListener;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.Locale;

/** Reserves only the part of the actual system bars still overlapping the WebView. */
@CapacitorPlugin(name = "ReaderDisplay")
public class ReaderDisplayPlugin extends Plugin {
    private ViewTreeObserver.OnGlobalLayoutListener layoutListener;
    private WebViewListener pageListener;
    private String lastScript;
    private String pendingScript;
    private int scriptGeneration;
    private boolean pageListenerRegistered;
    private String lastTheme;

    @Override
    public void load() {
        layoutListener = () -> publish();
        bridge.getWebView().getViewTreeObserver().addOnGlobalLayoutListener(layoutListener);
        pageListener = new WebViewListener() {
            @Override
            public void onPageStarted(WebView view) { resetDocument(); }
            @Override
            public void onPageCommitVisible(WebView view, String url) { resetDocument(); view.post(() -> publish()); }
            @Override
            public void onPageLoaded(WebView view) { resetDocument(); view.post(() -> publish()); }
        };
        bridge.getWebView().post(() -> publish());
    }

    @Override
    protected void handleOnStart() {
        if (!pageListenerRegistered) {
            bridge.addWebViewListener(pageListener);
            pageListenerRegistered = true;
        }
    }

    private void resetDocument() {
        scriptGeneration++;
        lastScript = null;
        pendingScript = null;
    }

    private JSObject measure() {
        WebView view = bridge.getWebView();
        View decor = getActivity().getWindow().getDecorView();
        WindowInsetsCompat rootInsets = ViewCompat.getRootWindowInsets(decor);
        if (rootInsets == null || view.getWidth() == 0 || decor.getWidth() == 0) return null;
        WindowInsetsCompat actualInsets = rootInsets;
        int[] window = new int[2];
        decor.getLocationOnScreen(window);
        Rect bounds = new Rect(window[0], window[1], window[0] + decor.getWidth(), window[1] + decor.getHeight());
        if (Build.VERSION.SDK_INT >= 30) {
            WindowMetrics metrics = getActivity().getWindowManager().getCurrentWindowMetrics();
            bounds = metrics.getBounds();
            actualInsets = WindowInsetsCompat.toWindowInsetsCompat(metrics.getWindowInsets(), decor);
        }
        Insets bars = actualInsets.getInsetsIgnoringVisibility(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
        int[] frame = new int[2];
        view.getLocationOnScreen(frame);
        float density = getActivity().getResources().getDisplayMetrics().density;
        boolean ime = rootInsets.isVisible(WindowInsetsCompat.Type.ime());
        float top = Math.max(0, bounds.top + bars.top - frame[1]) / density;
        float left = Math.max(0, bounds.left + bars.left - frame[0]) / density;
        float right = Math.max(0, frame[0] + view.getWidth() - (bounds.right - bars.right)) / density;
        float bottom = ime ? 0 : Math.max(0, frame[1] + view.getHeight() - (bounds.bottom - bars.bottom)) / density;
        JSObject result = new JSObject();
        result.put("top", top); result.put("right", right); result.put("bottom", bottom); result.put("left", left);
        result.put("density", density); result.put("imeVisible", ime);
        result.put("systemTopPx", bounds.top + bars.top);
        result.put("systemBottomPx", bounds.bottom - bars.bottom);
        result.put("webViewTopPx", frame[1]); result.put("webViewBottomPx", frame[1] + view.getHeight());
        return result;
    }

    private void publish() {
        JSObject insets = measure();
        if (insets == null) return;
        String script = String.format(Locale.US,
            "(function(){var root=document.documentElement;if(!root)return false;" +
            "root.style.setProperty('--reader-native-safe-top','%.3fpx');" +
            "root.style.setProperty('--reader-native-safe-right','%.3fpx');" +
            "root.style.setProperty('--reader-native-safe-bottom','%.3fpx');" +
            "root.style.setProperty('--reader-native-safe-left','%.3fpx');return true;})()",
            insets.optDouble("top"), insets.optDouble("right"), insets.optDouble("bottom"), insets.optDouble("left"));
        if (!script.equals(pendingScript) && (pendingScript != null || !script.equals(lastScript))) {
            int generation = ++scriptGeneration;
            pendingScript = script;
            bridge.getWebView().evaluateJavascript(script, result -> {
                if (generation != scriptGeneration) return;
                pendingScript = null;
                if ("true".equals(result)) lastScript = script;
            });
        }
    }

    private void applyBackground() {
        if (lastTheme != null) getActivity().getWindow().getDecorView().setBackgroundColor("dark".equals(lastTheme) ? Color.rgb(39, 45, 42) : Color.WHITE);
    }

    @PluginMethod
    public void getInsets(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            JSObject insets = measure();
            publish();
            if (insets == null) call.reject("WINDOW_INSETS_UNAVAILABLE"); else call.resolve(insets);
        });
    }

    @PluginMethod
    public void setTheme(PluginCall call) {
        String theme = call.getString("theme");
        if (!"light".equals(theme) && !"dark".equals(theme)) { call.reject("INVALID_DISPLAY_THEME"); return; }
        getActivity().runOnUiThread(() -> {
            lastTheme = theme;
            applyBackground();
            publish();
            call.resolve();
        });
    }

    @Override
    protected void handleOnResume() { bridge.getWebView().post(() -> { applyBackground(); publish(); }); }
    @Override
    protected void handleOnConfigurationChanged(Configuration config) { bridge.getWebView().post(() -> { applyBackground(); publish(); }); }
    @Override
    protected void handleOnDestroy() {
        if (layoutListener != null && bridge.getWebView().getViewTreeObserver().isAlive()) bridge.getWebView().getViewTreeObserver().removeOnGlobalLayoutListener(layoutListener);
        if (pageListener != null) bridge.removeWebViewListener(pageListener);
    }
}
