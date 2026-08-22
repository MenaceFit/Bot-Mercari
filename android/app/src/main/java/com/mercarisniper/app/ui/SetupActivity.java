package com.mercarisniper.app.ui;

import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.ProgressBar;

import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.textfield.TextInputEditText;
import com.google.android.material.textfield.TextInputLayout;
import com.mercarisniper.app.R;
import com.mercarisniper.app.data.Prefs;
import com.mercarisniper.app.data.SniperClient;

/**
 * Écran de connexion au bot.
 *
 * L'adresse est **testée** avant d'être enregistrée : si le bot ne répond
 * pas, on le dit tout de suite, avec la marche à suivre. C'est ce qui évite
 * de se retrouver devant une application vide sans savoir pourquoi.
 */
public class SetupActivity extends AppCompatActivity {

    private TextInputLayout layout;
    private TextInputEditText input;
    private MaterialButton connect;
    private ProgressBar progress;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);

        layout = findViewById(R.id.serverLayout);
        input = findViewById(R.id.serverInput);
        connect = findViewById(R.id.connectBtn);
        progress = findViewById(R.id.progress);

        String existing = Prefs.serverUrl(this);
        if (existing != null) {
            input.setText(existing);
        }

        connect.setOnClickListener(v -> attemptConnection());
        input.setOnEditorActionListener((view, actionId, event) -> {
            attemptConnection();
            return true;
        });
    }

    private void attemptConnection() {
        layout.setError(null);

        String raw = input.getText() == null ? "" : input.getText().toString();
        String url = Prefs.normalise(raw);
        if (url.isEmpty()) {
            layout.setError(getString(R.string.setup_error_empty));
            return;
        }

        setBusy(true);
        new SniperClient(url).ping(new SniperClient.Result<Boolean>() {
            @Override
            public void onSuccess(Boolean value) {
                Prefs.setServerUrl(SetupActivity.this, url);
                startActivity(new Intent(SetupActivity.this, MainActivity.class));
                finish();
            }

            @Override
            public void onFailure(String message) {
                setBusy(false);
                layout.setError(getString(R.string.setup_error_unreachable));
            }
        });
    }

    private void setBusy(boolean busy) {
        connect.setEnabled(!busy);
        connect.setText(busy ? R.string.setup_testing : R.string.setup_connect);
        progress.setVisibility(busy ? View.VISIBLE : View.GONE);
    }
}
