import { redirect } from "next/navigation";

// "Rendimiento de oportunidades" now lives inside "Rendimiento del modelo"; the old URL keeps working.
export default function OpportunityPerformance() {
  redirect("/model#acierto");
}
