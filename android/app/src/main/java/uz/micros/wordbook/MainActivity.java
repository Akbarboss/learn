package uz.micros.wordbook;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
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

import org.json.JSONObject;

import java.util.ArrayList;
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

    private static final int REQ_MIC = 7001;
    private SpeechRecognizer speech;
    private String pendingLang = "en-US";

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
        // The Web Speech API has no recognition side in a WebView, so the page
        // calls this bridge instead and gets its answer back through JS.
        web.addJavascriptInterface(new AndroidSpeechBridge(), "AndroidSpeech");
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

    /* ---------------- voice answers ---------------- */

    private class AndroidSpeechBridge {
        @JavascriptInterface
        public void start(String lang) {
            pendingLang = (lang == null || lang.isEmpty()) ? "en-US" : lang;
            runOnUiThread(() -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
                    return;   // listening starts once the answer comes back
                }
                listen(pendingLang);
            });
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (code != REQ_MIC) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            listen(pendingLang);
        } else {
            toJs("__speechError", "Microphone is off — type the answer instead");
        }
    }

    private void listen(String lang) {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            toJs("__speechError", "This phone has no speech recognition — type the answer");
            return;
        }
        if (speech != null) speech.destroy();
        speech = SpeechRecognizer.createSpeechRecognizer(this);

        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false);

        speech.setRecognitionListener(new RecognitionListener() {
            @Override public void onResults(Bundle b) {
                ArrayList<String> hits = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (hits != null && !hits.isEmpty()) toJs("__speechResult", hits.get(0));
                else toJs("__speechError", "Nothing was heard");
            }
            @Override public void onError(int err) {
                toJs("__speechError", err == SpeechRecognizer.ERROR_NO_MATCH
                        ? "Did not catch that — say it again"
                        : "Could not hear that — type the answer instead");
            }
            @Override public void onReadyForSpeech(Bundle b) {}
            @Override public void onBeginningOfSpeech() {}
            @Override public void onRmsChanged(float v) {}
            @Override public void onBufferReceived(byte[] buf) {}
            @Override public void onEndOfSpeech() {}
            @Override public void onPartialResults(Bundle b) {}
            @Override public void onEvent(int type, Bundle b) {}
        });
        speech.startListening(i);
    }

    /** Hand a value back to the page, escaped so quotes cannot break the call. */
    private void toJs(String fn, String arg) {
        final String js = "window." + fn + " && window." + fn + "("
                + JSONObject.quote(arg == null ? "" : arg) + ")";
        runOnUiThread(() -> web.evaluateJavascript(js, null));
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
        if (speech != null) {
            speech.destroy();
            speech = null;
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
