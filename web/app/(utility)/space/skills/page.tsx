import SkillsSection from "@/components/space/SkillsSection";

import StudentGuard from "@/components/space/StudentGuard";

export default function SpaceSkillsPage() {
  return (
    <StudentGuard>
      <SkillsSection />
    </StudentGuard>
  );
}
