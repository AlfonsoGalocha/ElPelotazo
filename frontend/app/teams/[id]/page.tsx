import { notFound } from "next/navigation";
import TeamProfileView from "@/components/analysis/TeamProfileView";

export default function TeamPage({ params }: { params: { id: string } }) {
  const teamId = Number(params.id);
  if (!Number.isInteger(teamId)) notFound();
  return <main><TeamProfileView teamId={teamId} /></main>;
}
