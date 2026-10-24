// FletcherVoice.cs — plays Fletcher from his head: bundled bark clips plus
// streamed PCM from fletcher.audio. Owned by the Fletcher module; Adam wires it.
//
// Setup (Adam):
//  1. Put this on Fletcher's head bone together with an AudioSource:
//     Spatial Blend = 1, Spatialize = on (Meta XR Audio spatializer), Play On Awake off.
//  2. Copy fletcher/barks/*.wav into Assets/Resources/Barks/ (file name = clip_id).
//  3. From your hub client, on the MAIN thread, call
//        fletcherVoice.HandleMessage(type, rawJson);
//     for every message whose type starts with "fletcher.".
//  4. Optional: drag the jaw bone into `jaw`; it opens with the loudness.
//
// Untested in a Quest build yet: try it on the first integration gate.

using System;
using System.Collections.Generic;
using UnityEngine;

[RequireComponent(typeof(AudioSource))]
public class FletcherVoice : MonoBehaviour
{
    [Tooltip("Folder inside a Resources folder that holds <clip_id>.wav barks")]
    public string barksResourcePath = "Barks";
    [Tooltip("Must match sample_rate in fletcher.speech (24000 = ElevenLabs pcm_24000)")]
    public int streamSampleRate = 24000;
    public float bufferSeconds = 30f;
    [Tooltip("Audio to collect before starting a streamed line, to ride out network jitter")]
    public float prebufferSeconds = 0.15f;

    [Header("Jaw (optional)")]
    public Transform jaw;
    public Vector3 jawAxis = Vector3.right;
    public float jawMaxAngle = 16f;
    public float jawGain = 8f;

    public event Action<string> Subtitle;  // hook a HUD caption here

    [Serializable] class SpeechData { public string utterance_id; public string text; public string clip_id; public bool stream; public int sample_rate; }
    [Serializable] class SpeechMsg { public string type; public SpeechData data; }
    [Serializable] class AudioData { public string utterance_id; public int index; public string pcm_b64; public bool final; }
    [Serializable] class AudioMsg { public string type; public AudioData data; }

    AudioSource streamSource;
    AudioSource barkSource;
    readonly Dictionary<string, AudioClip> barks = new Dictionary<string, AudioClip>();

    readonly object gate = new object();
    float[] ring;
    int readPos, writePos, count;
    bool waitingForPrebuffer = true;
    bool finalReceived;
    string currentUtterance;
    volatile float streamLevel;
    readonly float[] barkProbe = new float[256];
    Quaternion jawRest;
    float jawAngle;

    void Awake()
    {
        streamSource = GetComponent<AudioSource>();
        barkSource = gameObject.AddComponent<AudioSource>();
        barkSource.playOnAwake = false;
        barkSource.spatialBlend = streamSource.spatialBlend;
        barkSource.spatialize = streamSource.spatialize;
        barkSource.minDistance = streamSource.minDistance;
        barkSource.maxDistance = streamSource.maxDistance;
        barkSource.rolloffMode = streamSource.rolloffMode;
        barkSource.volume = streamSource.volume;
        barkSource.outputAudioMixerGroup = streamSource.outputAudioMixerGroup;

        foreach (var clip in Resources.LoadAll<AudioClip>(barksResourcePath))
            barks[clip.name] = clip;
        Debug.Log($"[FletcherVoice] loaded {barks.Count} barks");

        ring = new float[Mathf.CeilToInt(streamSampleRate * bufferSeconds)];
        // A looping streamed clip that pulls from our ring buffer; Unity resamples
        // 24 kHz to the output rate and the spatializer treats it like any clip.
        var clipLen = streamSampleRate; // 1 s loop; the callback decides the content
        streamSource.clip = AudioClip.Create("fletcher_stream", clipLen, 1, streamSampleRate, true, OnPcmRead);
        streamSource.loop = true;
        streamSource.Play();

        if (jaw) jawRest = jaw.localRotation;
    }

    /// Call on the main thread for every fletcher.* message.
    public void HandleMessage(string type, string json)
    {
        if (type == "fletcher.speech")
        {
            var d = JsonUtility.FromJson<SpeechMsg>(json).data;
            if (d == null) return;
            if (!string.IsNullOrEmpty(d.text)) Subtitle?.Invoke(d.text);
            if (!string.IsNullOrEmpty(d.clip_id))
            {
                if (barks.TryGetValue(d.clip_id, out var clip)) barkSource.PlayOneShot(clip);
                else Debug.LogWarning($"[FletcherVoice] missing bark {d.clip_id}");
            }
            else if (d.stream)
            {
                lock (gate)
                {
                    currentUtterance = d.utterance_id;
                    readPos = writePos = count = 0;
                    waitingForPrebuffer = true;
                    finalReceived = false;
                }
            }
        }
        else if (type == "fletcher.audio")
        {
            var d = JsonUtility.FromJson<AudioMsg>(json).data;
            if (d == null || d.utterance_id != currentUtterance) return;
            if (!string.IsNullOrEmpty(d.pcm_b64)) Push(Convert.FromBase64String(d.pcm_b64));
            if (d.final) lock (gate) finalReceived = true;
        }
    }

    void Push(byte[] pcm)
    {
        int samples = pcm.Length / 2;
        lock (gate)
        {
            for (int i = 0; i < samples; i++)
            {
                short s = (short)(pcm[2 * i] | (pcm[2 * i + 1] << 8));  // 16-bit little-endian
                ring[writePos] = s / 32768f;
                writePos = (writePos + 1) % ring.Length;
                if (count < ring.Length) count++;
                else readPos = (readPos + 1) % ring.Length;  // overflow: drop the oldest
            }
        }
    }

    // Unity's audio thread asks for samples here.
    void OnPcmRead(float[] data)
    {
        float sum = 0f;
        lock (gate)
        {
            int prebuffer = (int)(prebufferSeconds * streamSampleRate);
            if (waitingForPrebuffer && (count >= prebuffer || (finalReceived && count > 0)))
                waitingForPrebuffer = false;
            for (int i = 0; i < data.Length; i++)
            {
                if (!waitingForPrebuffer && count > 0)
                {
                    float v = ring[readPos];
                    readPos = (readPos + 1) % ring.Length;
                    count--;
                    data[i] = v;
                    sum += v * v;
                }
                else data[i] = 0f;
            }
            if (count == 0 && finalReceived) waitingForPrebuffer = true;
        }
        streamLevel = Mathf.Sqrt(sum / Mathf.Max(1, data.Length));
    }

    void Update()
    {
        if (!jaw) return;
        float level = streamLevel;
        if (barkSource.isPlaying)
        {
            barkSource.GetOutputData(barkProbe, 0);
            float s = 0f;
            foreach (var v in barkProbe) s += v * v;
            level = Mathf.Max(level, Mathf.Sqrt(s / barkProbe.Length));
        }
        float target = Mathf.Clamp01(level * jawGain) * jawMaxAngle;
        jawAngle = Mathf.Lerp(jawAngle, target, 1f - Mathf.Exp(-25f * Time.deltaTime));
        jaw.localRotation = jawRest * Quaternion.AngleAxis(jawAngle, jawAxis);
    }
}
