import PersonasSection from "@/components/space/PersonasSection";

import StudentGuard from "@/components/space/StudentGuard";

export default function SpacePersonasPage() {
  return (
    <StudentGuard>
      <PersonasSection />
    </StudentGuard>
  );
}
