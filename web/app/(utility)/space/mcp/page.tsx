import McpStoreSection from "@/components/space/McpStoreSection";

import StudentGuard from "@/components/space/StudentGuard";

export default function SpaceMcpPage() {
  return (
    <StudentGuard>
      <McpStoreSection />
    </StudentGuard>
  );
}
