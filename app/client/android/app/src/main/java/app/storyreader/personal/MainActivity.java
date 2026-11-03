package app.storyreader.personal;

import com.getcapacitor.BridgeActivity;
import android.os.Bundle;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ReaderCredentialsPlugin.class);
        registerPlugin(ReaderDisplayPlugin.class);
        registerPlugin(ReaderSpeechPlugin.class);
        registerPlugin(ReaderBackupPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
