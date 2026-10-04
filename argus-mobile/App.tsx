import React, { useState, useEffect, useRef } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  ScrollView,
  SafeAreaView,
  StatusBar,
  Animated,
  ActivityIndicator,
} from 'react-native';
import {
  useAudioRecorder,
  AudioModule,
  RecordingPresets,
} from 'expo-audio';
import * as Speech from 'expo-speech';
import * as FileSystem from 'expo-file-system';

const API_BASE = "https://argus-backend-production-e7bd.up.railway.app";
const AUTH_TOKEN = "default_secret_token"; // Ensure this matches your ARGUS_BEARER_TOKEN

interface LogEntry {
  sender: 'ARGUS' | 'KYLE' | 'SYSTEM' | 'ERROR';
  text: string;
}

export default function App() {
  const [logs, setLogs] = useState<LogEntry[]>([
    { sender: 'ARGUS', text: 'Mobile tactical link established. Tap Arc Core to speak directive.' }
  ]);
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [statusText, setStatusText] = useState("SYSTEM IDLE");

  // Modern expo-audio recording hook
  const audioRecorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const scrollViewRef = useRef<ScrollView | null>(null);

  // Request hardware microphone permissions on mount
  useEffect(() => {
    async function initAudio() {
      const status = await AudioModule.requestRecordingPermissionsAsync();
      if (!status.granted) {
        appendLog('ERROR', 'Microphone permission not granted.');
      }
    }
    initAudio();
  }, []);

  // Arc core pulse animation while recording or vocalizing
  useEffect(() => {
    if (isRecording || isSpeaking) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, {
            toValue: 1.25,
            duration: 600,
            useNativeDriver: true,
          }),
          Animated.timing(pulseAnim, {
            toValue: 0.95,
            duration: 600,
            useNativeDriver: true,
          }),
        ])
      ).start();
    } else {
      pulseAnim.stopAnimation();
      Animated.timing(pulseAnim, {
        toValue: 1,
        duration: 200,
        useNativeDriver: true,
      }).start();
    }
  }, [isRecording, isSpeaking]);

  const appendLog = (sender: LogEntry['sender'], text: string) => {
    setLogs(prev => [...prev, { sender, text }]);
    setTimeout(() => {
      scrollViewRef.current?.scrollToEnd({ animated: true });
    }, 100);
  };

  const startVoiceRecording = async () => {
    // Barge-in: immediately cut off any ongoing assistant speech
    if (isSpeaking) {
      Speech.stop();
      setIsSpeaking(false);
    }

    try {
      await AudioModule.setAudioModeAsync({
        allowsRecording: true,
        playsInSilentMode: true,
      });

      await audioRecorder.prepareToRecordAsync();
      audioRecorder.record();

      setIsRecording(true);
      setStatusText("LISTENING...");
    } catch (err: any) {
      appendLog('ERROR', `Recording init failed: ${err.message}`);
    }
  };

  const stopVoiceRecording = async () => {
    setIsRecording(false);
    setIsProcessing(true);
    setStatusText("TRANSCRIBING (WHISPER)...");

    try {
      await audioRecorder.stop();
      const uri = audioRecorder.uri;

      if (!uri) throw new Error("No audio URI captured from microphone.");

      // Native multipart binary stream upload
      const uploadResponse = await FileSystem.uploadAsync(`${API_BASE}/transcribe`, uri, {
        fieldName: 'file',
        httpMethod: 'POST',
        uploadType: (FileSystem as any).UploadType?.MULTIPART || (FileSystem as any).FileSystemUploadType?.MULTIPART || 1,
        headers: {
          'Authorization': `Bearer ${AUTH_TOKEN.trim()}`,
        },
      });

      if (uploadResponse.status < 200 || uploadResponse.status >= 300) {
        throw new Error(`Transcribe error (HTTP ${uploadResponse.status}):${uploadResponse.body}`);
      }

      const transData = JSON.parse(uploadResponse.body);
      const transcribedText = (transData.text || "").trim();

      if (!transcribedText) {
        appendLog('SYSTEM', 'Whisper completed: No clear speech recognized.');
        setStatusText("SYSTEM IDLE");
        setIsProcessing(false);
        return;
      }

      await sendDirective(transcribedText);

    } catch (err: any) {
      appendLog('ERROR', err.message);
      setStatusText("SYSTEM IDLE");
    } finally {
      setIsProcessing(false);
    }
  };

  const sendDirective = async (message: string) => {
    appendLog('KYLE', message);
    setStatusText("PROCESSING DIRECTIVE...");

    try {
      const chatRes = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${AUTH_TOKEN.trim()}`,
        },
        body: JSON.stringify({ message }),
      });

      if (!chatRes.ok) {
        const errPayload = await chatRes.json().catch(() => ({}));
        throw new Error(errPayload.detail || `Chat error: HTTP ${chatRes.status}`);
      }

      const chatData = await chatRes.json();
      const reply = chatData.reply || "Directive executed.";

      appendLog('ARGUS', reply);
      vocalizeResponse(reply);

    } catch (err: any) {
      appendLog('ERROR', err.message);
      setStatusText("SYSTEM IDLE");
    }
  };

  const vocalizeResponse = (text: string) => {
    // Strip markdown formatting and raw URLs for smooth vocal delivery
    const cleanText = text
      .replace(/[*_#`~\[\]\(\)]/g, "")
      .replace(/https?:\/\/\S+/g, "link provided")
      .trim();

    if (!cleanText) {
      setStatusText("SYSTEM IDLE");
      return;
    }

    setIsSpeaking(true);
    setStatusText("VOCALIZING FEED...");

    Speech.speak(cleanText, {
      rate: 1.05,
      pitch: 0.95,
      onDone: () => {
        setIsSpeaking(false);
        setStatusText("SYSTEM IDLE");
      },
      onError: () => {
        setIsSpeaking(false);
        setStatusText("SYSTEM IDLE");
      },
    });
  };

  const handleCorePress = () => {
    if (isProcessing) return;

    if (isSpeaking) {
      Speech.stop();
      setIsSpeaking(false);
      setStatusText("SYSTEM IDLE");
      return;
    }

    if (isRecording) {
      stopVoiceRecording();
    } else {
      startVoiceRecording();
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="light-content" />

      {/* Header Bar */}
      <View style={styles.header}>
        <Text style={styles.brandTitle}>A.R.G.U.S. // MOBILE</Text>
        <View style={styles.statusBadge}>
          <Text style={styles.statusText}>{statusText}</Text>
        </View>
      </View>

      {/* Arc Reactor Interaction Core */}
      <View style={styles.coreContainer}>
        <TouchableOpacity
          activeOpacity={0.8}
          onPress={handleCorePress}
          style={styles.reactorTouchArea}
        >
          <View style={[styles.outerRing, isRecording && styles.outerRingRecording]} />
          <Animated.View
            style={[
              styles.innerRing,
              isRecording && styles.innerRingRecording,
              { transform: [{ scale: pulseAnim }] },
            ]}
          >
            <View style={[styles.reactorCore, isRecording && styles.coreRecording]}>
              {isProcessing && <ActivityIndicator color="#050b14" />}
            </View>
          </Animated.View>
        </TouchableOpacity>
        <Text style={styles.coreActionLabel}>
          {isRecording
            ? "RECORDING... TAP TO TRANSMIT"
            : isSpeaking
            ? "SPEAKING... TAP TO INTERRUPT"
            : isProcessing
            ? "COMMUNICATING WITH RAILWAY..."
            : "TAP CORE TO ISSUE DIRECTIVE"}
        </Text>
      </View>

      {/* Terminal View */}
      <View style={styles.terminalPanel}>
        <ScrollView
          ref={scrollViewRef}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={true}
        >
          {logs.map((entry, idx) => (
            <View key={idx} style={styles.logRow}>
              <Text
                style={[
                  styles.logSender,
                  entry.sender === 'KYLE' && styles.senderUser,
                  entry.sender === 'ERROR' && styles.senderError,
                  entry.sender === 'SYSTEM' && styles.senderSystem,
                ]}
              >
                {entry.sender}:
              </Text>
              <Text style={styles.logText}>{entry.text}</Text>
            </View>
          ))}
        </ScrollView>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050b14',
  },
  header: {
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0, 240, 255, 0.2)',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: 'rgba(4, 12, 24, 0.95)',
  },
  brandTitle: {
    color: '#00f0ff',
    fontSize: 16,
    fontWeight: 'bold',
    letterSpacing: 2,
  },
  statusBadge: {
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderWidth: 1,
    borderColor: 'rgba(0, 240, 255, 0.3)',
    borderRadius: 3,
    backgroundColor: 'rgba(0, 240, 255, 0.05)',
  },
  statusText: {
    color: '#00f0ff',
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1,
  },
  coreContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 26,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0, 240, 255, 0.15)',
  },
  reactorTouchArea: {
    width: 170,
    height: 170,
    alignItems: 'center',
    justifyContent: 'center',
  },
  outerRing: {
    position: 'absolute',
    width: 160,
    height: 160,
    borderRadius: 80,
    borderWidth: 2,
    borderColor: 'rgba(0, 240, 255, 0.3)',
    borderStyle: 'dashed',
  },
  outerRingRecording: {
    borderColor: 'rgba(255, 184, 77, 0.6)',
  },
  innerRing: {
    position: 'absolute',
    width: 120,
    height: 120,
    borderRadius: 60,
    borderWidth: 2,
    borderColor: '#00f0ff',
    alignItems: 'center',
    justifyContent: 'center',
  },
  innerRingRecording: {
    borderColor: '#ffb84d',
  },
  reactorCore: {
    width: 68,
    height: 68,
    borderRadius: 34,
    backgroundColor: '#00f0ff',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#00f0ff',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 15,
    elevation: 10,
  },
  coreRecording: {
    backgroundColor: '#ffb84d',
    shadowColor: '#ffb84d',
  },
  coreActionLabel: {
    color: '#00f0ff',
    fontSize: 11,
    fontWeight: 'bold',
    letterSpacing: 1.5,
    marginTop: 18,
  },
  terminalPanel: {
    flex: 1,
    margin: 12,
    backgroundColor: 'rgba(6, 20, 36, 0.85)',
    borderWidth: 1,
    borderColor: 'rgba(0, 240, 255, 0.2)',
    borderRadius: 6,
    overflow: 'hidden',
  },
  scrollContent: {
    padding: 16,
    gap: 12,
  },
  logRow: {
    flexDirection: 'column',
    gap: 3,
  },
  logSender: {
    color: '#00f0ff',
    fontSize: 11,
    fontWeight: 'bold',
    letterSpacing: 1,
  },
  senderUser: {
    color: '#ffb84d',
  },
  senderSystem: {
    color: '#88a0b0',
  },
  senderError: {
    color: '#ff4d4d',
  },
  logText: {
    color: '#e0f7fa',
    fontSize: 13,
    lineHeight: 18,
  },
});