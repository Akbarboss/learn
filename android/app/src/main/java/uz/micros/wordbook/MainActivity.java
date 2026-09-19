package uz.micros.wordbook;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.net.Uri;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

import java.util.Locale;

/**
 * A thin shell around the wordbook website.
 *
 * Nothing about the lessons lives in this app — it only opens the page in
 * {@code R.string.site_url}. Edit the content on GitHub Pages and every phone
 * gets it on the next launch, with no new APK.
 */
public class MainActivity extends Activity {

    private WebView web;
    private String siteUrl;
    private TextToSpeech tts;
    private boolean ttsReady;
    private String pendingSpeech;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        siteUrl = getString(R.string.site_url);

        web = new WebView(this);
        web.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        web.addJavascriptInterface(new AndroidTtsBridge(), "AndroidTts");
        // Keeps the learned-word marks and the learner's name between launches.
        // Without this the page loads but forgets everything — the classic WebView bug.
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);

        // Let the page's own dark-theme CSS follow the phone's theme
        // instead of the WebView inverting the colours itself.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(s, true);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                return false;   // keep every link inside the app
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                // Only the very first launch without internet lands here; after that
                // the service worker serves the cached copy.
                if (req.isForMainFrame()) {
                    v.loadUrl("file:///android_asset/offline.html?u=" + Uri.encode(siteUrl));
                }
            }
        });

        if (state != null) {
            web.restoreState(state);
        } else {
            web.loadUrl(siteUrl);
        }

        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) {
                int language = tts.setLanguage(Locale.US);
                ttsReady = language != TextToSpeech.LANG_MISSING_DATA
                        && language != TextToSpeech.LANG_NOT_SUPPORTED;
                if (ttsReady && pendingSpeech != null) {
                    speakNow(pendingSpeech);
                    pendingSpeech = null;
                }
            }
        });
    }

    private void speakNow(String text) {
        if (tts == null || !ttsReady || text == null || text.trim().isEmpty()) return;
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "wordbook-speech");
    }

    private class AndroidTtsBridge {
        @JavascriptInterface
        public void speak(String text) {
            runOnUiThread(() -> {
                if (ttsReady) speakNow(text);
                else pendingSpeech = text;
            });
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        web.saveState(state);
    }

    @Override
    protected void onDestroy() {
        if (tts != null) {
            tts.stop();
            tts.shutdown();
        }
        if (web != null) web.destroy();
        super.onDestroy();
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent event) {
        if (code == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(code, event);
    }
}
