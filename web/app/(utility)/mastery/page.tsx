"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  CourseScopeChip,
  useCourseScope,
} from "@/components/courses/CourseScope";
import { CreateTopicWizard } from "@/components/space/learning/CreateTopicWizard";
import type { Translate } from "@/components/space/learning/format";
import { topicDisplayName } from "@/components/space/learning/format";
import { TopicAtlas } from "@/components/space/learning/TopicAtlas";
import {
  fetchAllProgress,
  fetchMasteryTopics,
  type MasteryTopic,
} from "@/lib/learning-api";

function MasteryPathRoute() {
  const router = useRouter();
  const { t } = useTranslation();
  const [topics, setTopics] = useState<MasteryTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const wizardTriggerRef = useRef<HTMLElement | null>(null);
  // path_id → current_stage. The topic payload does not carry the stage; the
  // progress summaries do, and the cards use it for the diagnostic hand-off.
  const [stages, setStages] = useState<Record<string, string>>({});
  // Present when opened from a course page or a Course Study hand-off. It both
  // narrows the atlas to that course's paths and adopts whatever is built here.
  const scope = useCourseScope();

  const loadTopics = useCallback(async () => {
    setError(null);
    try {
      setTopics(await fetchMasteryTopics({ cache: "no-store" }));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("The atlas could not be loaded."),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadStages = useCallback(async () => {
    try {
      const { summaries } = await fetchAllProgress();
      setStages(
        Object.fromEntries(
          summaries.map((summary) => [summary.book_id, summary.current_stage]),
        ),
      );
    } catch {
      // Stage hints are progressive enhancement — the atlas renders without
      // them when the summaries endpoint is unavailable.
    }
  }, []);

  useEffect(() => {
    void loadTopics();
    void loadStages();
  }, [loadTopics, loadStages]);

  const handleTopicDeleted = useCallback((pathId: string) => {
    setTopics((previous) =>
      previous.filter((topic) => topic.path_id !== pathId),
    );
    setStages((previous) => {
      const next = { ...previous };
      delete next[pathId];
      return next;
    });
  }, []);

  const handleTopicImported = useCallback(
    (_pathId: string, _moduleCount: number) => {
      // The import filled the route server-side; reload so the card drops its
      // empty-topic guidance and shows the real map.
      setLoading(true);
      void loadTopics();
      void loadStages();
    },
    [loadTopics, loadStages],
  );

  // A course that references no path yet scopes the atlas to nothing, and the
  // empty state then invites building the first one — which is the move that
  // was previously a dead end.
  const scopedTopics = useMemo(() => {
    if (!scope) return topics;
    const allowed = new Set(scope.refIds("mastery_path"));
    return topics.filter((topic) => allowed.has(topic.path_id));
  }, [scope, topics]);

  return (
    <>
      <TopicAtlas
        topics={scopedTopics}
        loading={loading}
        error={error}
        scopeChip={scope ? <CourseScopeChip scope={scope} /> : null}
        stages={stages}
        onTopicDeleted={handleTopicDeleted}
        onTopicImported={handleTopicImported}
        onCreate={(trigger) => {
          wizardTriggerRef.current = trigger;
          setWizardOpen(true);
        }}
        onRetry={() => {
          setLoading(true);
          void loadTopics();
          void loadStages();
        }}
      />
      {wizardOpen && (
        <CreateTopicWizard
          returnFocusRef={wizardTriggerRef}
          onClose={() => setWizardOpen(false)}
          onCreated={async (topic) => {
            setWizardOpen(false);
            await scope?.attach(
              "mastery_path",
              topic.path_id,
              topicDisplayName(topic, t as Translate),
            );
            router.push(`/mastery/${encodeURIComponent(topic.path_id)}`);
          }}
        />
      )}
    </>
  );
}

export default function MasteryPathPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      }
    >
      <MasteryPathRoute />
    </Suspense>
  );
}
