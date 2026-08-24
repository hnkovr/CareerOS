"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

const RELATIONSHIPS = ["recruiter", "hiring_manager", "client", "peer", "other"] as const;

export default function ContactsPage() {
  const [showNew, setShowNew] = useState(false);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [relationship, setRelationship] = useState<(typeof RELATIONSHIPS)[number]>("recruiter");
  const queryClient = useQueryClient();

  const contacts = useQuery({
    queryKey: ["contacts", q],
    queryFn: async () =>
      unwrap(await api.GET("/api/contacts", { params: { query: q ? { q } : {} } })),
  });

  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/contacts", {
          body: { name, company_name: company || null, email: email || null, relationship },
        }),
      ),
    onSuccess: () => {
      setName("");
      setCompany("");
      setEmail("");
      setShowNew(false);
      queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });

  const setNextAction = useMutation({
    mutationFn: async ({ id, next_action }: { id: string; next_action: string }) =>
      unwrap(await api.PATCH("/api/contacts/{contact_id}", { params: { path: { contact_id: id } }, body: { next_action } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["contacts"] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-bold">Contacts</h1>
        <div className="flex items-center gap-2">
          <input className="input w-56" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-primary" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Close" : "Add contact"}
          </button>
        </div>
      </div>

      {showNew && (
        <Card title="New contact">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <input className="input" placeholder="Name *" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="Company" value={company} onChange={(e) => setCompany(e.target.value)} />
            <input className="input" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <select className="input" value={relationship} onChange={(e) => setRelationship(e.target.value as never)}>
              {RELATIONSHIPS.map((r) => (
                <option key={r} value={r}>
                  {r.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <button className="btn btn-primary mt-3" onClick={() => create.mutate()} disabled={!name.trim() || create.isPending}>
            Save
          </button>
          {create.isError && <ErrorBox error={create.error} />}
        </Card>
      )}

      <Card>
        {contacts.isPending ? (
          <Spinner />
        ) : (contacts.data ?? []).length === 0 ? (
          <Empty>No contacts yet.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-dim">
                <th className="pb-2">Name</th>
                <th className="pb-2">Company</th>
                <th className="pb-2">Email</th>
                <th className="pb-2">Relationship</th>
                <th className="pb-2">Last contact</th>
                <th className="pb-2">Next action</th>
              </tr>
            </thead>
            <tbody>
              {(contacts.data ?? []).map((c) => (
                <tr key={c.id} className="border-t border-line/60">
                  <td className="py-2 pr-2 font-medium">{c.name}</td>
                  <td className="py-2 pr-2 text-ink-dim">{c.company_name ?? "—"}</td>
                  <td className="py-2 pr-2 text-ink-dim">{c.email ?? "—"}</td>
                  <td className="py-2 pr-2">
                    <Badge tone={c.relationship === "recruiter" ? "accent" : "neutral"}>{c.relationship.replaceAll("_", " ")}</Badge>
                  </td>
                  <td className="py-2 pr-2 text-xs text-ink-dim">{formatDate(c.last_contact_at)}</td>
                  <td className="py-2">
                    <input
                      className="input px-2 py-1 text-xs"
                      defaultValue={c.next_action ?? ""}
                      placeholder="—"
                      onBlur={(e) => {
                        if (e.target.value !== (c.next_action ?? "")) {
                          setNextAction.mutate({ id: c.id, next_action: e.target.value });
                        }
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
