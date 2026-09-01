"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiUrl } from "@/lib/api";
import {
  html5PlayerController,
  youtubePlayerController,
  type PlayerController,
  type YouTubePlayerLike,
} from "@/lib/video-player-controller";
import type { VideoPlayback } from "@/lib/video-learning-api";

interface YouTubeNamespace {
  Player: new (
    element: HTMLElement,
    options: {
      videoId: string;
      host: string;
      width: string;
      height: string;
      playerVars: { origin: string; playsinline: 1; rel: 0 };
      events: {
        onReady(event: { target: YouTubePlayerLike }): void;
        onStateChange(event: { data: number }): void;
        onError(event: { data: number }): void;
      };
    },
  ) => YouTubePlayerLike;
}

declare global {
  interface Window {
    YT?: YouTubeNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let youtubeApiPromise: Promise<YouTubeNamespace> | null = null;
const YOUTUBE_API_SRC = "https://www.youtube.com/iframe_api";

function loadYouTubeApi(): Promise<YouTubeNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (youtubeApiPromise) return youtubeApiPromise;
  youtubeApiPromise = new Promise((resolve, reject) => {
    let settled = false;
    const finish = (namespace?: YouTubeNamespace, error?: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      if (namespace) resolve(namespace);
      else {
        youtubeApiPromise = null;
        reject(error || new Error("YouTube Player API did not initialize."));
      }
    };
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      try {
        previous?.();
      } finally {
        finish(window.YT?.Player ? window.YT : undefined);
      }
    };
    const timeout = window.setTimeout(
      () => finish(undefined, new Error("YouTube Player API timed out.")),
      10_000,
    );
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${YOUTUBE_API_SRC}"]`,
    );
    const script = existing || document.createElement("script");
    script.addEventListener(
      "error",
      () => {
        script.remove();
        finish(undefined, new Error("YouTube Player API could not be loaded."));
      },
      { once: true },
    );
    if (!existing) {
      script.src = YOUTUBE_API_SRC;
      script.async = true;
      document.head.appendChild(script);
    }
  });
  return youtubeApiPromise;
}

interface WatchingPlayerProps {
  playback: VideoPlayback;
  transcriptLanguage: string;
  onController(controller: PlayerController | null): void;
  onTime(seconds: number, duration: number): void;
  onPersist(): void;
  onError(message: string): void;
}

export function WatchingPlayer(props: WatchingPlayerProps) {
  if (props.playback.kind === "bilibili_iframe") {
    return <BilibiliPlayer {...props} playback={props.playback} />;
  }
  return props.playback.kind === "youtube_iframe" ? (
    <YouTubePlayer {...props} playback={props.playback} />
  ) : (
    <InvidiousPlayer {...props} playback={props.playback} />
  );
}

/** Bilibili embed: the official external player has no JS API — we can start
 *  at a timestamp (remount on seek) but cannot track live position. Same
 *  honest contract as BilibiliReadingPlayer in the reading workspace. */
function BilibiliPlayer({
  playback,
  onController,
  onTime,
}: WatchingPlayerProps & {
  playback: Extract<VideoPlayback, { kind: "bilibili_iframe" }>;
}) {
  const { t } = useTranslation();
  const [start, setStart] = useState(playback.start_seconds);
  const timeRef = useRef(playback.start_seconds);
  const controller = useMemo(
    () => ({
      currentTime: () => timeRef.current,
      duration: () => Number.POSITIVE_INFINITY,
      seek: (seconds: number) => {
        const next = Math.max(0, seconds);
        timeRef.current = next;
        setStart(next);
        onTime(next, Number.POSITIVE_INFINITY);
      },
      play: () => undefined,
      pause: () => undefined,
      destroy: () => undefined,
      tracksPosition: false,
    }),
    [onTime],
  );
  useEffect(() => {
    onController(controller);
    onTime(timeRef.current, Number.POSITIVE_INFINITY);
    return () => onController(null);
  }, [controller, onController, onTime]);
  return (
    <div className="relative h-full w-full">
      <iframe
        src={`https://player.bilibili.com/player.html?bvid=${playback.bvid}&page=${playback.page}&autoplay=0&start=${Math.floor(start)}`}
        title="Bilibili"
        className="aspect-video h-full w-full border-0 bg-black"
        allow="autoplay; fullscreen; picture-in-picture"
        allowFullScreen
        referrerPolicy="strict-origin-when-cross-origin"
      />
      <div className="pointer-events-none absolute right-2 bottom-2 rounded bg-slate-900/70 px-2 py-1 text-xs text-slate-200">
        {t("Bilibili 播放器不支持进度跟随；答疑不受影响。")}
      </div>
    </div>
  );
}

function YouTubePlayer({
  playback,
  onController,
  onTime,
  onPersist,
  onError,
}: WatchingPlayerProps & {
  playback: Extract<VideoPlayback, { kind: "youtube_iframe" }>;
}) {
  const { t } = useTranslation();
  const playerRootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const playerRoot = playerRootRef.current;
    if (!playerRoot) return;
    const mount = document.createElement("div");
    mount.style.width = "100%";
    mount.style.height = "100%";
    playerRoot.replaceChildren(mount);
    let cancelled = false;
    let controller: PlayerController | null = null;
    let timer = 0;
    void loadYouTubeApi()
      .then((YT) => {
        if (cancelled) return;
        new YT.Player(mount, {
          videoId: playback.video_id,
          host: "https://www.youtube-nocookie.com",
          width: "100%",
          height: "100%",
          playerVars: {
            origin: window.location.origin,
            playsinline: 1,
            rel: 0,
          },
          events: {
            onReady: (event) => {
              if (cancelled) return;
              controller = youtubePlayerController(event.target);
              onController(controller);
              if (playback.start_seconds > 0)
                controller.seek(playback.start_seconds);
              timer = window.setInterval(
                () =>
                  onTime(
                    controller?.currentTime() || 0,
                    controller?.duration() || 0,
                  ),
                250,
              );
            },
            onStateChange: (event) => {
              if (cancelled) return;
              if (event.data === 0 || event.data === 2) onPersist();
            },
            onError: (event) => {
              if (!cancelled)
                onError(
                  t("YouTube playback failed ({{code}}).", {
                    code: event.data,
                  }),
                );
            },
          },
        });
      })
      .catch((caught) => {
        if (!cancelled) {
          onError(
            caught instanceof Error
              ? t(caught.message)
              : t("YouTube playback failed."),
          );
        }
      });
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      onController(null);
      controller?.destroy();
      playerRoot.replaceChildren();
    };
  }, [
    onController,
    onError,
    onPersist,
    onTime,
    playback.start_seconds,
    playback.video_id,
    t,
  ]);

  return (
    <div
      ref={playerRootRef}
      className="aspect-video w-full bg-black"
      title={t("YouTube learning video")}
    />
  );
}

function InvidiousPlayer({
  playback,
  onController,
  onTime,
  onPersist,
  onError,
  transcriptLanguage,
}: WatchingPlayerProps & {
  playback: Extract<VideoPlayback, { kind: "html5" }>;
}) {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const controller = html5PlayerController(video);
    onController(controller);
    const report = () =>
      onTime(controller.currentTime(), controller.duration());
    const ready = () => {
      if (playback.start_seconds > 0) controller.seek(playback.start_seconds);
      report();
    };
    video.addEventListener("loadedmetadata", ready);
    video.addEventListener("timeupdate", report);
    video.addEventListener("pause", onPersist);
    video.addEventListener("ended", onPersist);
    return () => {
      video.removeEventListener("loadedmetadata", ready);
      video.removeEventListener("timeupdate", report);
      video.removeEventListener("pause", onPersist);
      video.removeEventListener("ended", onPersist);
      onController(null);
      controller.destroy();
    };
  }, [onController, onPersist, onTime, playback.start_seconds]);

  return (
    <video
      ref={videoRef}
      controls
      playsInline
      className="aspect-video w-full bg-black"
      src={apiUrl(playback.stream_url)}
      onError={() =>
        onError(t("The configured Invidious stream could not be played."))
      }
    >
      <track
        kind="subtitles"
        srcLang={transcriptLanguage}
        src={apiUrl(playback.subtitles_url)}
        default
      />
    </video>
  );
}
