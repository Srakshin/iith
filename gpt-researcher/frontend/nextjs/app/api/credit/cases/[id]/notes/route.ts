import { NextResponse } from "next/server";


const backendUrl = process.env.NEXT_PUBLIC_GPTR_API_URL || "http://localhost:8000";


async function parseJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}


export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  try {
    const body = await request.json();
    const response = await fetch(`${backendUrl}/api/credit/cases/${params.id}/notes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await parseJson(response);
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error(
      `POST /api/credit/cases/${params.id}/notes - Error proxying to backend:`,
      error
    );
    return NextResponse.json(
      { error: "Failed to connect to backend service" },
      { status: 500 }
    );
  }
}
