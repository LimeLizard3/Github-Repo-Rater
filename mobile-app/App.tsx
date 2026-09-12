import { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';

// Routed through an ngrok tunnel (see `ngrok http 8000 --domain=...`),
// not a direct local-network address -- this stays fixed no matter what
// network the laptop is on, as long as the tunnel and the backend server
// are both running. https, no port: ngrok terminates TLS on 443 and
// forwards internally to localhost:8000.
const API_BASE_URL = 'https://preamble-statute-rising.ngrok-free.dev';

// Accepts "owner/repo", or a pasted "https://github.com/owner/repo" URL,
// and pulls out just the two names web_app.py's /api/rate expects.
function parseOwnerRepo(input: string): { owner: string; repo: string } | null {
  const cleaned = input
    .trim()
    .replace(/^https?:\/\/(www\.)?github\.com\//i, '')
    .replace(/\/+$/, ''); // drop a trailing slash, if any

  const parts = cleaned.split('/').filter(Boolean);
  if (parts.length !== 2) return null;
  return { owner: parts[0], repo: parts[1] };
}

export default function App() {
  // React state: whenever one of these changes (via its setter function,
  // e.g. setRepoInput), React automatically re-runs this component and
  // re-renders the screen with the new value -- same idea as a Python
  // variable, except reassigning it also triggers a UI refresh.
  const [repoInput, setRepoInput] = useState(''); //Stores the variable AND redraws the screen
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleRatePress() {
    const parsed = parseOwnerRepo(repoInput);
    if (!parsed) {
      setErrorMessage('Enter a repo as "owner/repo" (or paste a GitHub URL).');
      return;
    }

    setLoading(true);
    setErrorMessage(null);

    try {
      const response = await fetch(`${API_BASE_URL}/api/rate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      });

      const contentType = response.headers.get('content-type') || '';

      if (!response.ok || contentType.includes('application/json')) {
        // Every error path in web_app.py returns {"error": "..."} JSON --
        // same shape whether it's a 400, 404, or 429.
        const body = await response.json();
        setErrorMessage(body.error || `Request failed (${response.status}).`);
        return;
      }

      // Success: the response body is the raw PDF file's bytes, not JSON.
      // arrayBuffer() collects those raw bytes; Uint8Array is just a JS
      // view onto them that File.write() knows how to accept directly --
      // no re-encoding needed, unlike older Expo versions.
      const bytes = new Uint8Array(await response.arrayBuffer());

      const file = new File(Paths.document, `${parsed.owner}__${parsed.repo}.pdf`);
      file.create({ overwrite: true }); // makes the (empty) file on disk
      file.write(bytes); // then fills it with the PDF's bytes

      // Hands the saved file to the phone's native share sheet, where the
      // user picks what to do with it -- save to Files, open in a PDF
      // viewer, AirDrop, etc. There's no "download folder" on a phone the
      // way there is on a computer, so this is the standard way an app
      // hands a generated file back to the person using it.
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(file.uri, { mimeType: 'application/pdf' });
      } else {
        Alert.alert('Saved', `PDF saved to ${file.uri}`);
      }
    } catch (err) {
      setErrorMessage('Could not reach the server -- is it running, and is your phone on the same wifi?');
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Github Repo Analyzer</Text>

      <TextInput
        style={styles.input}
        placeholder="owner/repo"
        placeholderTextColor="#888"
        autoCapitalize="none"
        autoCorrect={false}
        value={repoInput}
        onChangeText={setRepoInput}
        editable={!loading}
      />

      <Pressable
        style={[styles.button, loading && styles.buttonDisabled]}
        onPress={handleRatePress}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color="#121212" />
        ) : (
          <Text style={styles.buttonText}>Rate</Text>
        )}
      </Pressable>

      {errorMessage && <Text style={styles.error}>{errorMessage}</Text>}

      <StatusBar style="light" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#121212',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 16,
  },
  title: {
    color: '#fff',
    fontSize: 22,
    fontWeight: '600',
    marginBottom: 8,
  },
  input: {
    width: '100%',
    backgroundColor: '#1e1e1e',
    color: '#fff',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#333',
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
  },
  button: {
    width: '100%',
    backgroundColor: '#4fd1c5',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: '#121212',
    fontSize: 16,
    fontWeight: '600',
  },
  error: {
    color: '#ff6b6b',
    textAlign: 'center',
  },
});
