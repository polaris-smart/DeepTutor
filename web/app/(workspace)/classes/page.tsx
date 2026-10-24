"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Plus, RefreshCw, School, Trash2, Users, X } from "lucide-react";
import { fetchAuthStatus } from "@/lib/auth";
import {
  createClass,
  deleteClass,
  fetchClassRosters,
  updateClass,
  type ClassRoster,
} from "@/lib/class-insights-api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

// Teacher-facing page; students are redirected away (the backend enforces the
// same gate with 403 on every /classes endpoint).
const ALLOWED_ROLES = new Set(["admin", "teacher"]);

export default function ClassesPage() {
  const router = useRouter();
  const { t } = useTranslation();

  const [rosters, setRosters] = useState<ClassRoster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // 新建班级表单：班名 + 学生名册（逐个加入，暂存后随创建一起提交）。
  const [name, setName] = useState("");
  const [draftStudents, setDraftStudents] = useState<string[]>([]);
  const [studentInput, setStudentInput] = useState("");
  const [creating, setCreating] = useState(false);

  // 名册编辑：每个班一张「添加输入」状态；增删即时 PUT 全量名册。
  const [rosterInput, setRosterInput] = useState<Record<string, string>>({});
  const [savingClass, setSavingClass] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ClassRoster | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadRosters = useCallback(async () => {
    try {
      const data = await fetchClassRosters();
      setRosters(data.classes);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to load classes"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (!status?.authenticated) {
        router.replace("/login");
        return;
      }
      if (!ALLOWED_ROLES.has(status.role ?? "")) {
        router.replace("/");
        return;
      }
      void loadRosters();
    });
  }, [router, loadRosters]);

  const addDraftStudent = () => {
    const value = studentInput.trim();
    if (!value) return;
    setDraftStudents((prev) =>
      prev.includes(value) ? prev : [...prev, value],
    );
    setStudentInput("");
  };

  const submitCreate = async () => {
    setError("");
    setNotice("");
    if (!name.trim()) {
      setError(t("Enter a class name"));
      return;
    }
    setCreating(true);
    try {
      const result = await createClass({
        name: name.trim(),
        students: draftStudents,
      });
      setRosters((prev) => [...prev, result.class]);
      setNotice(t("Class created"));
      setName("");
      setDraftStudents([]);
      setStudentInput("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to create class"));
    } finally {
      setCreating(false);
    }
  };

  /** 用全量名册 PUT（后端语义：整体替换），成功后以返回值刷新本地。 */
  const saveRoster = async (roster: ClassRoster, students: string[]) => {
    setError("");
    setNotice("");
    setSavingClass(roster.id);
    try {
      const result = await updateClass(roster.id, { students });
      setRosters((prev) =>
        prev.map((item) => (item.id === roster.id ? result.class : item)),
      );
      setNotice(t("Class updated"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to update class"));
    } finally {
      setSavingClass(null);
    }
  };

  const addStudent = async (roster: ClassRoster) => {
    const value = (rosterInput[roster.id] ?? "").trim();
    if (!value || roster.students.includes(value)) {
      setRosterInput((prev) => ({ ...prev, [roster.id]: "" }));
      return;
    }
    await saveRoster(roster, [...roster.students, value]);
    setRosterInput((prev) => ({ ...prev, [roster.id]: "" }));
  };

  const removeStudent = async (roster: ClassRoster, student: string) => {
    await saveRoster(
      roster,
      roster.students.filter((name) => name !== student),
    );
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setError("");
    setNotice("");
    setDeleting(true);
    try {
      await deleteClass(pendingDelete.id);
      setRosters((prev) => prev.filter((item) => item.id !== pendingDelete.id));
      setNotice(t("Class deleted"));
      setPendingDelete(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to delete class"));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="h-screen overflow-y-auto bg-[var(--background)] px-4 py-10 [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-4xl">
        <div className="mb-8">
          <Link
            href="/"
            className="mb-4 inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition-colors"
          >
            <ArrowLeft size={16} />
            {t("Back")}
          </Link>
          <h1 className="font-serif text-xl font-semibold text-[var(--foreground)]">
            {t("Classes")}
          </h1>
          <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
            {t("Create classes and manage student rosters for assignments")}
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-400">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600 dark:text-emerald-400">
            {notice}
          </div>
        )}

        {/* 新建班级：班名 + 学生名册（学生账号用户名，创建时由后端校验） */}
        <section className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-sm">
          <h2 className="font-serif text-base font-semibold text-[var(--foreground)]">
            {t("New class")}
          </h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[var(--muted-foreground)]">
                {t("Class name")}
              </span>
              <input
                type="text"
                value={name}
                maxLength={100}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("e.g. Grade 7, Class 2")}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1.5 text-sm text-[var(--foreground)] outline-none transition-colors hover:border-teal-500/40 focus:border-teal-500/60"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[var(--muted-foreground)]">
                {t("Add student")}
              </span>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={studentInput}
                  onChange={(e) => setStudentInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addDraftStudent();
                    }
                  }}
                  placeholder={t("Student username")}
                  className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1.5 text-sm text-[var(--foreground)] outline-none transition-colors hover:border-teal-500/40 focus:border-teal-500/60"
                />
                <button
                  type="button"
                  onClick={addDraftStudent}
                  className="flex shrink-0 items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--background)] transition-colors"
                >
                  <Plus size={14} />
                  {t("Add")}
                </button>
              </div>
            </label>
          </div>
          {draftStudents.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {draftStudents.map((student) => (
                <span
                  key={student}
                  className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)]/60 px-2.5 py-1 text-xs text-[var(--foreground)]"
                >
                  {student}
                  <button
                    type="button"
                    onClick={() =>
                      setDraftStudents((prev) =>
                        prev.filter((name) => name !== student),
                      )
                    }
                    className="text-[var(--muted-foreground)] hover:text-red-500 transition-colors"
                    aria-label={t("Remove")}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="mt-4">
            <button
              onClick={submitCreate}
              disabled={creating || !name.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-[var(--foreground)] px-4 py-2 text-sm font-medium text-[var(--background)] transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <School size={14} />
              {creating ? t("Creating…") : t("Create class")}
            </button>
            <p className="mt-2 text-xs text-[var(--muted-foreground)]">
              {t("Student usernames are validated when the class is saved.")}
            </p>
          </div>
        </section>

        {/* 班级列表：名册增删即时保存 */}
        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-serif text-base font-semibold text-[var(--foreground)]">
              {t("My classes")}
            </h2>
            <button
              onClick={() => void loadRosters()}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm border border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--card)] transition-colors"
            >
              <RefreshCw size={14} />
              {t("Refresh")}
            </button>
          </div>

          {loading ? (
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] px-6 py-14 text-center text-sm text-[var(--muted-foreground)] shadow-sm">
              {t("Loading…")}
            </div>
          ) : rosters.length === 0 ? (
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] px-6 py-14 text-center shadow-sm">
              <Users
                size={28}
                strokeWidth={1.5}
                className="mx-auto text-[var(--muted-foreground)]/50"
              />
              <p className="mt-3 text-sm font-medium text-[var(--foreground)]">
                {t("No classes yet")}
              </p>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                {t("Create your first class above to start assigning homework.")}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {rosters.map((roster) => (
                <div
                  key={roster.id}
                  className="rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-5 py-3">
                    <div>
                      <h3 className="text-sm font-semibold text-[var(--foreground)]">
                        {roster.name}
                      </h3>
                      <p className="text-xs text-[var(--muted-foreground)]">
                        {t("{{count}} students", { count: roster.students.length })}
                        {` · ${t("Created {{date}}", {
                          date: roster.created_at.slice(0, 10),
                        })}`}
                      </p>
                    </div>
                    <button
                      onClick={() => setPendingDelete(roster)}
                      className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-[var(--muted-foreground)] hover:text-red-600 dark:hover:text-red-400 transition-colors"
                    >
                      <Trash2 size={13} />
                      {t("Delete class")}
                    </button>
                  </div>
                  <div className="px-5 py-4">
                    <span className="mb-2 block text-xs font-medium text-[var(--muted-foreground)]">
                      {t("Roster")}
                    </span>
                    {roster.students.length === 0 ? (
                      <p className="text-sm text-[var(--muted-foreground)]">
                        {t("Roster is empty")}
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {roster.students.map((student) => (
                          <span
                            key={student}
                            className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)]/60 px-2.5 py-1 text-xs text-[var(--foreground)]"
                          >
                            {student}
                            <button
                              type="button"
                              disabled={savingClass === roster.id}
                              onClick={() => void removeStudent(roster, student)}
                              className="text-[var(--muted-foreground)] hover:text-red-500 transition-colors disabled:opacity-40"
                              aria-label={t("Remove")}
                            >
                              <X size={12} />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-3 flex gap-2">
                      <input
                        type="text"
                        value={rosterInput[roster.id] ?? ""}
                        onChange={(e) =>
                          setRosterInput((prev) => ({
                            ...prev,
                            [roster.id]: e.target.value,
                          }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void addStudent(roster);
                          }
                        }}
                        placeholder={t("Student username")}
                        className="w-56 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1.5 text-sm text-[var(--foreground)] outline-none transition-colors hover:border-teal-500/40 focus:border-teal-500/60"
                      />
                      <button
                        type="button"
                        disabled={savingClass === roster.id}
                        onClick={() => void addStudent(roster)}
                        className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--background)] transition-colors disabled:opacity-50"
                      >
                        <Plus size={14} />
                        {savingClass === roster.id ? t("Saving…") : t("Add")}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("Delete class")}
        tone="danger"
        busy={deleting}
        busyLabel={t("Deleting…")}
        confirmLabel={t("Delete")}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      >
        {pendingDelete
          ? t("Delete the class {{name}}? Student accounts are not affected.", {
              name: pendingDelete.name,
            })
          : null}
      </ConfirmDialog>
    </div>
  );
}
