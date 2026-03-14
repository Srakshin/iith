import { NextResponse } from "next/server";


const backendUrl = process.env.NEXT_PUBLIC_GPTR_API_URL || "http://localhost:8000";


async function parseJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}


export async function GET() {
  try {
    const response = await fetch(`${backendUrl}/api/credit/cases`);
    const data = await parseJson(response);
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("GET /api/credit/cases - Error proxying to backend:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend service" },
      { status: 500 }
    );
  }
}


export async function POST(request: Request) {
  try {
    const body = await request.json();
    const response = await fetch(`${backendUrl}/api/credit/cases`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await parseJson(response);
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("POST /api/credit/cases - Error proxying to backend:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend service" },
      { status: 500 }
    );
  }
}
