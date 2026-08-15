import CliAppsSection from "@/components/cli-apps/CliAppsSection";

import StudentGuard from "@/components/space/StudentGuard";

export default function SpaceCliAppsPage() {
  return (
    <StudentGuard>
      <CliAppsSection />
    </StudentGuard>
  );
}
